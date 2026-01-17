## Orange Pi Zero 2W (Armbian) Audio Setup

### Overview
Running Linux Voice Assistant on Orange Pi Zero 2W with Armbian requires special attention to audio configuration. The default soundcard setup may not work out-of-the-box, leading to errors like:

```
AssertionError
  File ".../soundcard/pulseaudio.py", line 101, in __init__
    assert self._pa_context_get_state(self.context)==_pa.PA_CONTEXT_READY
```

#### Steps to Resolve

1. **Check PulseAudio/ALSA Installation**
   - Ensure PulseAudio is installed and running:
     ```sh
     sudo apt-get install pulseaudio
     pulseaudio --start
     ```
   - For ALSA-only systems, install `alsa-utils`:
     ```sh
     sudo apt-get install alsa-utils
     ```

2. **Verify Audio Devices**
   - List ALSA devices:
     ```sh
     aplay -l
     arecord -l
     ```
   - For PulseAudio:
     ```sh
     pactl list short sources
     pactl list short sinks
     ```

3. **Test Audio Input/Output**
   - Record and play a test file:
     ```sh
     arecord -D plughw:0,0 -f cd test.wav
     aplay test.wav
     ```

4. **Configure Default Device**
   - If PulseAudio is not available, set ALSA as default by creating or editing `/etc/asound.conf` or `~/.asoundrc`:
     ```
     defaults.pcm.card 0
     defaults.ctl.card 0
     ```

5. **Troubleshooting**
   - If you see `AssertionError` from `soundcard`, PulseAudio may not be running or accessible. Try running as a non-root user, or ensure the user is in the `audio` group.
   - If `wpctl` is missing, install `wireplumber` or `pipewire` if you want to use PipeWire, but PulseAudio is usually simpler for Armbian.
   - Reboot after installing audio packages.

6. **Run LVA with Device Listing**
   - Use:
     ```sh
     script/run --name Test --list-input-devices
     script/run --name Test --list-output-devices
     ```
   - Specify device index or name with `--audio-input-device` and `--audio-output-device`.

### References
- See [README.md](../README.md#audio-device-configuration) for more details.

If audio issues persist, check dmesg for hardware errors, and consult Armbian/Orange Pi forums for board-specific quirks.

# Linux Voice Assistant – AI Agent Coding Guide

## Project Architecture
- **Multi-instance voice satellite** for Home Assistant using the ESPHome protocol.
- **Core files:**
  - `satellite.py`: Main protocol logic, voice event handling, audio streaming, TTS, wake word, timer, and Pi LED feedback.
  - `api_server.py`: ESPHome protocol base server, message parsing, authentication.
  - `__main__.py`: CLI entry, preferences loading, wake word discovery, event loop setup.
  - `mpv_player.py`: Audio playback with music ducking.
  - `entity.py`: ESPHome entities (media player, text attributes) for Home Assistant.
  - `models.py`: Preferences, wake word, and runtime state dataclasses.
  - `zeroconf.py`: mDNS/zeroconf discovery for Home Assistant.
  - `script/`: Python scripts for setup, deployment, and management (not shell scripts).

## Data Flow
- Audio → Wake word detection → Audio stream to Home Assistant → STT/intent/TTS → TTS playback (with music ducking) → Visual feedback (LED on Pi).
- All async/event-driven via a single asyncio event loop.
- Central state: `ServerState` (see `models.py`).

## Developer Workflows
- **Setup:** `script/setup` (venv + deps)
- **Run (dev):** `script/run --name NAME`
- **Deploy (prod):** `script/deploy NAME` (systemd user service, auto-assigns port/MAC)
- **Manage:** `script/status`, `script/restart`, `script/stop NAME`, `script/remove NAME`
- **Test/Lint:** `script/test`, `script/format`, `script/lint`
- **Audio devices:** `script/run --list-input-devices` / `--list-output-devices`

## Configuration & Patterns
- **Preferences:**
  - Per-instance CLI: `preferences/user/{NAME}_cli.json`
  - Per-instance: `preferences/user/{NAME}.json` (wake words)
  - Global: `preferences/user/ha_settings.json`
- **Wake words:**
  - Discovered from `wakewords/` (`*.json` + `.tflite`)
  - Types: microWakeWord, openWakeWord (see `models.py`)
- **Entities:**
  - Subclass `ESPHomeEntity` (see `entity.py`), register in `ServerState.entities`
- **Message handling:**
  - `APIServer.handle_message()` (sync, dispatches to entities)
- **Music ducking:**
  - `mpv_player.py` + `entity.py` (announcement lowers music volume)
- **Raspberry Pi LED:**
  - Controlled via `/sys/class/leds/PWR/trigger` (see `satellite.py`)

## Conventions & Gotchas
- All scripts in `script/` are Python, venv-aware, and manage systemd services.
- Ports auto-assigned from 6053 (override with `LVAS_BASE_PORT`).
- MAC addresses auto-generated if not set.
- Preferences migration handled on first run.
- Audio input must be 16kHz mono.
- Use `ServerState.audio_queue` for thread-safe audio.
- Legacy wrapper scripts are deprecated (use `script/deploy`).

## Examples
- Add a new wake word: extend `WakeWordType` in `models.py`, add config in `wakewords/`.
- Add a new entity: subclass `ESPHomeEntity` in `entity.py`, register in `ServerState.entities`.
- Add a voice event: implement in `satellite.py`'s message dispatcher.

Refer to the [README.md](../README.md) for full setup and usage details.
python -m linux_voice_assistant --name "Test" --list-input-devices
