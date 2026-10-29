
# Debugging

## Explanation of the audio system:

The Linux audio stack consists of multiple layers working together:

```
┌─────────────────────────────────────────────────────────────┐
│                     HOST SYSTEM                             │
│  ┌─────────────────────────────────────────────────────┐    │
│  │              User Applications                      │    │
│  │  (Browser, Media Player, Voice Assistant, etc.)     │    │
│  └────────────────┬────────────────────────────────────┘    │
│                   │                                         │
│  ┌────────────────▼────────────────────────────────────┐    │
│  │         PipeWire / PulseAudio                       │    │
│  │         (Sound Server)                              │    │
│  │  - Audio mixing & routing                           │    │
│  │  - Device abstraction                               │    │
│  │  - Network audio support                            │    │
│  │  - Per-application volume control                   │    │
│  └────────────────┬────────────────────────────────────┘    │
│                   │                                         │
│  ┌────────────────▼────────────────────────────────────┐    │
│  │              ALSA                                   │    │
│  │  (Advanced Linux Sound Architecture)                │    │
│  │  - Kernel-level audio driver framework              │    │
│  │  - Direct hardware access                           │    │
│  │  - Controls: /dev/snd/                              │    │
│  └────────────────┬────────────────────────────────────┘    │
│                   │                                         │
│  ┌────────────────▼────────────────────────────────────┐    │
│  │           Hardware (Sound Card)                     │    │
│  │   ┌─────────┐  ┌─────────┐  ┌───────────────────┐   │    │
│  │   │Microphon│  │ Speaker │  │   DSP/Codec       │   │    │
│  │   └─────────┘  └─────────┘  └───────────────────┘   │    │
│  └─────────────────────────────────────────────────────┘    │
│                                                             │
│  LVA_XDG_RUNTIME_DIR=/run/user/1000                             │
│  └── Socket: /run/user/1000/pipewire-0                      │
│      Socket: /run/user/1000/pulse/native                    │
└─────────────────────────────────────────────────────────────┘
                              │
                              │ Device Passthrough
                              ▼
┌─────────────────────────────────────────────────────────────┐
│                   DOCKER CONTAINER                          │
│  ┌─────────────────────────────────────────────────────┐    │
│  │              Container Process                      │    │
│  │  (Linux Voice Assistant)                            │    │
│  │                                                     │    │
│  │  Accesses audio via:                                │    │
│  │  - PipeWire/PulseAudio client library               │    │
│  │  - Uses same LVA_XDG_RUNTIME_DIR socket path            │    │
│  │                                                     │    │
│  │  Environment Variables:                             │    │
│  │  - LVA_XDG_RUNTIME_DIR=/run/user/1000 (host path)       │    │
│  │  - AUDIO_INPUT_DEVICE="<specific audio input device name>" │    │
│  │  - AUDIO_OUTPUT_DEVICE="pulse"                      │    │
│  └─────────────────────────────────────────────────────┘    │
│                                                             │
│  Device Access:                                             │
│  - Socket: /run/user/1000/pipewire-0 (volume mount)         │
│  - Socket: /run/user/1000/pulse/native (volume mount)       │
└─────────────────────────────────────────────────────────────┘
```

### How it works:

1. **Host Layer**:
   - ALSA provides kernel drivers for audio hardware
   - PipeWire/PulseAudio sits on top of ALSA as a sound server
   - Applications connect to PipeWire/PulseAudio, not directly to ALSA, otherwhise the container would need access to the ALSA devices. If you use ALSA only only one application can connect to the device at a time.

2. **Docker Integration**:
   - The LVA_XDG_RUNTIME_DIR socket must be mounted from host to container
   - The container user must have matching UID/GID with host user or the audio group is added.

3. **Communication Flow**:
   ```
   Container App → PipeWire Client Library → Host PipeWire/PulseAudio
         ↓                                         ↓
   (via mounted socket)                    (via ALSA drivers)
         ↓                                         ↓
   ┌─────────────────────────────────────────────────────┐
   │              Audio Data flows to Hardware           │
   └─────────────────────────────────────────────────────┘
   ```

4. **Key Configuration Points**:
   - `LVA_XDG_RUNTIME_DIR` must point to host's runtime directory
   - Microphone and the default `pulse` output only use the socket `pulse/native`. The socket `pipewire-0` is only needed for `pipewire/...` output devices.
   - Container user UID/GID should match host user

### Docker Compose Example:

```yaml
services:
  voice-assistant:
    image: ghcr.io/ohf-voice/linux-voice-assistant:latest
    environment:
      - LVA_XDG_RUNTIME_DIR=/run/user/1000
      - AUDIO_INPUT_DEVICE=<specific audio input device name>
      - AUDIO_OUTPUT_DEVICE=pulse
    volumes:
      - /run/user/1000:/run/user/1000:rw
    devices:
      - /dev/snd:/dev/snd
    group_add:
      - audio
```


## List available audio devices:

To list available audio devices, run:

```bash
# List audio devices
docker run --rm -it -e LIST_DEVICES="1" ghcr.io/florian-asche/linux-voice-assistant:develop-final-docker-version
```

Update the `.env` file with your device names:

``` ini
AUDIO_INPUT_DEVICE="Built-in Audio"
AUDIO_OUTPUT_DEVICE="pulse"
```

`AUDIO_INPUT_DEVICE` takes part of the device name from the input device list. Leave it unset to use the default source of the audio server.

The output list contains the same device several times, once per mpv audio driver (`pipewire/...`, `pulse/...`, `alsa/...`). Use the `pulse/...` entry, for example `AUDIO_OUTPUT_DEVICE="pulse/alsa_output.usb-Example.analog-stereo"`. `AUDIO_OUTPUT_DEVICE="pulse"` selects the default PulseAudio output without a device name.


## Pipewire runtime directory (Optional):

If you work with the root user, you need to:
``` sh
export LVA_XDG_RUNTIME_DIR=/run/user/${USERLVA_USER_ID
```

💡 **Note:** Replace `$USERLVA_USER_IDth your actual user id that you want to run the voice assistant.


## Troubleshooting:

### Audio Device Not Found:

If the container cannot access audio devices, ensure:

1. The USERLVA_USER_ID`.env` matches your actual users ID (run `id -u $USER` to check)
2. The LVA_XDG_RUNTIME_DIR path exists on the host

### Permission Denied Errors:

tbd

### Low Audio Quality:

If you experience audio quality issues:

1. Check the audio device settings with `alsamixer`
2. Try setting `AUDIO_INPUT_DEVICE` and `AUDIO_OUTPUT_DEVICE` explicitly
3. Adjust the microphone gain in your system settings


## More debugging documentation:

See [PiCompose](https://github.com/florian-asche/PiCompose/blob/main/docs/pipewire_debugging.md) for more debugging information.
