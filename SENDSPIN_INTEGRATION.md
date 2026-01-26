# SendSpin Integration

Linux Voice Assistant now supports receiving synchronized audio streams from SendSpin servers, allowing the MediaPlayer entity to switch between Home Assistant music and SendSpin streams.

## Overview

The SendSpin integration adds the ability for LVA to act as a SendSpin client:
- **Home Assistant music**: Continues to play through MPV as before
- **SendSpin streams**: Play through sounddevice when a SendSpin server starts streaming
- **Automatic switching**: SendSpin interrupts Home Assistant music when it starts
- **State synchronization**: MediaPlayerEntity accurately reflects which source is playing

## Architecture

```
┌────────────────────────────────────────────────┐
│   Linux Voice Assistant                        │
│                                                │
│   Input: soundcard (microphone)                │
│                                                │
│   Output (MediaPlayerEntity):                  │
│   ┌──────────────────────────────────────┐    │
│   │  MPV Player (music_player)           │    │
│   │  - Home Assistant music              │    │
│   │  - TTS responses                     │    │
│   │  - Announcements                     │    │
│   └──────────────────────────────────────┘    │
│                                                │
│   ┌──────────────────────────────────────┐    │
│   │  SendSpin Bridge                     │    │
│   │  - sounddevice output                │    │
│   │  - Interrupts MPV when active        │    │
│   │  - Updates MediaPlayerEntity state   │    │
│   └──────────────────────────────────────┘    │
└────────────────────────────────────────────────┘
                    ↓
         ┌──────────────────────┐
         │  SendSpin Server     │
         │  (streaming audio)   │
         └──────────────────────┘
```

## Installation

Install the additional system dependency:

```bash
# On Debian/Ubuntu/Raspberry Pi:
sudo apt-get install libportaudio2

# On other systems: https://www.portaudio.com/
```

Python dependencies are automatically included:
- `aiosendspin~=3.0` - SendSpin protocol client
- `sounddevice>=0.4.6` - Audio device interface for SendSpin playback

## Usage

### Basic Usage (Home Assistant only)

Without SendSpin arguments, LVA works exactly as before:

```bash
python3 -m linux_voice_assistant --name "Kitchen"
```

### With SendSpin Server

To enable SendSpin streaming, provide a server URL:

```bash
python3 -m linux_voice_assistant \
    --name "Kitchen" \
    --sendspin-url ws://192.168.1.100:8928/sendspin
```

### Configuration Options

| Option | Description | Default |
|--------|-------------|---------|
| `--sendspin-url` | SendSpin server WebSocket URL | None (SendSpin disabled) |
| `--sendspin-client-id` | Unique identifier for this client | `linux-voice-assistant-<hostname>` |
| `--sendspin-static-delay-ms` | Playback delay in milliseconds | 0.0 |

### Example

```bash
python3 -m linux_voice_assistant \
    --name "Living Room" \
    --host 0.0.0.0 \
    --port 6053 \
    --audio-input-device "Echo-Cancel Source" \
    --audio-output-device "pipewire/echo-cancel-sink" \
    --sendspin-url ws://192.168.1.10:8928/sendspin \
    --sendspin-client-id "living-room-assistant" \
    --sendspin-static-delay-ms -120
```

## How It Works

### Source Switching

1. **Home Assistant plays music** → MPV plays, MediaPlayerEntity state = PLAYING
2. **SendSpin server starts streaming** → SendSpin bridge:
   - Stops MPV playback
   - Starts sounddevice stream
   - Updates MediaPlayerEntity state = PLAYING
3. **SendSpin stream ends** → SendSpin bridge:
   - Stops sounddevice stream
   - Updates MediaPlayerEntity state = IDLE
   - Home Assistant can resume playing if needed

### MediaPlayerEntity Integration

The MediaPlayerEntity in Home Assistant always reflects the current playback state:
- Shows PLAYING when either source is active
- Shows IDLE when both sources are idle
- Shows the correct volume level
- Responds to pause/play/volume commands (affects MPV player)

### Audio Output

- **MPV**: Plays Home Assistant music, TTS, announcements through the device specified by `--audio-output-device`
- **sounddevice**: Plays SendSpin streams through the system default audio device
- Both use the same physical output in typical setups

## Behavior

### When SendSpin Starts Streaming

- Any Home Assistant music currently playing is stopped
- SendSpin audio plays through sounddevice
- MediaPlayerEntity state shows PLAYING
- Home Assistant sees the entity as busy

### When SendSpin Stops Streaming

- SendSpin audio stops
- MediaPlayerEntity state shows IDLE
- Home Assistant can send new music if desired

### TTS and Announcements

- Always play through MPV (original behavior)
- Work regardless of SendSpin state
- Not affected by SendSpin streams

## Troubleshooting

### SendSpin Audio Not Playing

1. Ensure PortAudio is installed: `apt-get install libportaudio2`
2. Check connection to server: verify `--sendspin-url` is correct
3. Test default audio device: `python3 -c "import sounddevice as sd; sd.play([0.1]*44100, 44100); sd.wait()"`
4. Enable debug logging: `--debug`

### Connection Issues

1. Verify server URL format: `ws://hostname:port/sendspin`
2. Check network connectivity to server
3. Ensure SendSpin server is running
4. Check firewall settings

### Sync Issues

If SendSpin audio is out of sync with other clients:

1. Adjust `--sendspin-static-delay-ms` (typically negative values like `-100` to `-150`)
2. Check network latency: `ping <server-ip>`
3. Use wired Ethernet instead of WiFi

### Debug Logging

Enable debug output to see SendSpin connection and streaming details:

```bash
python3 -m linux_voice_assistant --name "Kitchen" \
    --sendspin-url ws://192.168.1.100:8928/sendspin \
    --debug
```

## Changes from Original LVA

This integration adds SendSpin support while maintaining full backward compatibility:

**What's New:**
- SendSpin client that can receive audio streams
- Automatic source switching between Home Assistant and SendSpin
- Three new optional CLI arguments

**What's Unchanged:**
- Original MPV-based playback for Home Assistant music
- TTS and announcement handling
- Wake word detection
- Audio input via soundcard
- All existing CLI arguments and behavior

**Dependencies Added:**
- `aiosendspin~=3.0`
- `sounddevice>=0.4.6`
- System: `libportaudio2`

## Related Projects

- [SendSpin Protocol](https://www.sendspin-audio.com) - Official website
- [sendspin-cli](https://github.com/Sendspin/sendspin-cli) - Reference client implementation
- [aiosendspin](https://github.com/Sendspin/aiosendspin) - Python library

## Credits

SendSpin is a project from the [Open Home Foundation](https://www.openhomefoundation.org/).
