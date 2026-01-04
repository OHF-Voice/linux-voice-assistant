# Linux Voice Assistant - AI Agent Instructions

## Project Overview
A multi-instance Linux voice satellite for Home Assistant using the ESPHome protocol. Supports wake words, announcements, timers, and conversations through Home Assistant's voice pipeline. Designed to run as systemd services on servers and Raspberry Pi devices.

## Architecture

### Core Components
- **`satellite.py`**: Main `VoiceSatelliteProtocol` class (inherits from `APIServer`) implementing ESPHome server protocol. Handles voice events, audio streaming, TTS playback, wake word management, and timer events. Includes Raspberry Pi-specific LED control for visual feedback during voice interactions.
- **`api_server.py`**: Base `APIServer` class implementing ESPHome network protocol (packet parsing, message handling, protobuf serialization, authentication). Provides `handle_message()` abstract method for subclasses.
- **`__main__.py`**: Entry point with CLI parsing, wake word discovery from filesystem, preferences loading with migration support, audio device enumeration, and asyncio event loop setup. Runnable as module: `python -m linux_voice_assistant --name Test`
- **`mpv_player.py`**: `MpvMediaPlayer` wrapper using python-mpv for audio playback with automatic music ducking (lowers volume during announcements). Manages playlists and done callbacks.
- **`entity.py`**: ESPHome entities exposed to Home Assistant (`MediaPlayerEntity` for audio control, `TextAttributeEntity` for displaying active STT/TTS/assistant text in HA UI).
- **`models.py`**: Data classes for preferences (`Preferences`, `GlobalPreferences`), wake words (`AvailableWakeWord`, `WakeWordType`), and runtime state (`ServerState` - central state container passed through the app).
- **`zeroconf.py`**: Zeroconf/mDNS service discovery for automatic Home Assistant detection.
- **`script/*`**: Zero-dependency Python scripts (not shell scripts) for deployment, management, and development. All executable with `#!/usr/bin/env python3`.

### Data Flow
1. Audio captured from microphone → wake word detection (pymicro-wakeword/pyopen-wakeword)
2. On wake → `VoiceAssistantAudio` messages stream audio chunks to Home Assistant via ESPHome protocol
3. Home Assistant processes STT/intent/TTS → sends back `VoiceAssistantEventResponse` events and audio URL
4. TTS audio downloaded and played via mpv, with music ducking if media player is active
5. Events trigger visual feedback on Raspberry Pi (LED control via `/sys/class/leds/PWR/trigger`)

### Script System Architecture
All scripts in `script/` are **Python executables** (not shell scripts) with zero external dependencies beyond stdlib. Key design principles:
- **Self-contained**: Parse CLI args, read/write JSON configs, manage systemd services, all without imports from the main package
- **Venv-aware**: Auto-detect `.venv/` and use appropriate Python interpreter (see [script/run](script/run#L51-L53))
- **Template cascade**: `script/run` and `script/deploy` use intelligent defaults (user → OS-specific → main) via `_choose_template()` function
- **Auto-assignment**: Ports (starting from 6053, or `LVAS_BASE_PORT` env var) and MAC addresses (via `uuid.getnode()`) auto-generated if not specified
- **Preference migration**: Legacy single-file preferences automatically split into per-instance + global on first run
- **Systemd integration**: `script/deploy` enables user linger and installs services in `~/.config/systemd/user/`

## Multi-Instance Configuration

### Preferences System (Two-Tier)
The `preferences/user/` directory is created on first run. Each instance has two config files:
- **Per-instance CLI config** (`preferences/user/{NAME}_cli.json`): CLI args, port, MAC address, system info, autostart flag
- **Per-instance preferences** (`preferences/user/{NAME}.json`): Active wake words list only (minimal by design - see `Preferences` dataclass in [models.py](linux_voice_assistant/models.py#L52-L56))
- **Shared/global** (`preferences/user/ha_settings.json`): HA base URL, token, friendly names, history entity (see `GlobalPreferences` in [models.py](linux_voice_assistant/models.py#L59-L66))

### Template Cascade
`script/run` loads defaults with priority:
1. `preferences/default/default_user_cli.json` (user custom defaults)
2. `preferences/default/default_wsl_cli.json` (OS-specific, e.g., WSL)
3. `preferences/default/default_cli.json` (main fallback)

When creating a new instance, `script/run` auto-generates a unique MAC address and port (starting from 6053, incrementing for each instance).

## Developer Workflows

### Setup & Run
```bash
script/setup              # Create venv, install dependencies
script/run --name "MyVA"  # Auto-creates preferences/user/{NAME}_cli.json and {NAME}.json
```

For direct module execution (after venv setup):
```bash
source .venv/bin/activate  # Or let scripts auto-detect venv
python -m linux_voice_assistant --name "Test" --list-input-devices
```

**Note**: `script/setup` can optionally install dev dependencies with `--dev` flag for linting/testing tools.

### Deployment (Production)
```bash
# Deploy and auto-start one or more instances as systemd user services
script/deploy MyVA1 MyVA2 --audio-input-device 0  # Deploy multiple with shared overrides
script/deploy --name MyVA --port 6055             # Deploy single with custom port
```
**Important**: `script/deploy` is the primary deployment tool. It:
- Creates preference files (like `script/run`)
- Enables systemd user linger (persistent user sessions across reboots)
- Installs systemd user service for each instance (`~/.config/systemd/user/{NAME}.service`)
- Starts services immediately
- Sets `autostart: true` by default in CLI config
- Creates convenience symlinks in `preferences/user/` pointing to systemd unit files

**Note**: Older deployments may use manual wrapper scripts (like `lvas_01_wrapper.py`). These are superseded by `script/deploy` but may exist in legacy setups.

### Instance Management
```bash
script/status             # Show all instances and their service status
script/restart            # Restart running instance(s)
script/stop <NAME>        # Stop instance(s)
script/remove <NAME>      # Remove instance config and service
```

### Testing & Linting
```bash
script/test               # Run pytest in tests/
script/format             # black + isort formatting
script/lint               # black --check, isort --check, flake8, pylint, mypy
```

## Key Patterns

### Wake Word Loading
Wake words discovered from `wakewords/` subdirectories by scanning `*.json` config files. Two types:
- **microWakeWord**: Config file itself is the model (`.json` contains model data)
- **openWakeWord**: Config references separate `.tflite` model file

Example config (`wakewords/okay_nabu.json`):
```json
{
  "type": "micro",
  "wake_word": "Okay Nabu",
  "trained_languages": ["en"]
}
```

Loaded via `AvailableWakeWord.load()` in [models.py](linux_voice_assistant/models.py#L31-L49), which dynamically imports and instantiates the correct wake word detector class. The `stop.json`/`stop.tflite` model is special-cased and not shown as selectable in HA UI.

### Audio Device Selection
Use `--list-input-devices` / `--list-output-devices` to discover devices. Microphone must support 16kHz mono.

### ESPHome Protocol
Communication uses protobuf messages from `aioesphomeapi.api_pb2`. Key messages:
- `VoiceAssistantEventResponse`: Voice pipeline events (STT start/end, TTS start/end, intent results)
- `VoiceAssistantAudio`: Audio chunks sent to HA during conversation
- `VoiceAssistantTimerEventResponse`: Timer events (started, updated, finished)
- `VoiceAssistantConfigurationRequest`: Wake word configuration exchange

Message handling flow: [api_server.py](linux_voice_assistant/api_server.py#L47-L78) `process_packet()` → `handle_message()` (implemented in [satellite.py](linux_voice_assistant/satellite.py)) → `send_messages()`.

### Raspberry Pi LED Feedback
On Raspberry Pi hardware, visual feedback provided via power LED:
- **Idle**: LED off (`none` trigger)
- **Listening**: LED solid on (`default-on` trigger)
- **Processing**: LED heartbeat pattern (`heartbeat` trigger)

Detection via `/proc/device-tree/model` or `/proc/cpuinfo`. LED control in [satellite.py](linux_voice_assistant/satellite.py#L56-L99) `_set_led()` function.

### Logging & History
- Conversation history logged to `lvas_log` (symlinked to `/dev/shm/lvas_log` for RAM-based logging to reduce disk wear)
- History synced to HA via REST API if `ha_token` and `ha_history_entity` configured in `ha_settings.json`
- Log entries formatted as "User: {stt_text}" and "{assistant_name}: {tts_text}"

## Dependencies
- **aioesphomeapi**: ESPHome API protocol implementation
- **soundcard**: Audio input (requires `portaudio19-dev`)
- **pymicro-wakeword** / **pyopen-wakeword**: Wake word detection
- **python-mpv**: Audio output (requires `libmpv-dev`)

## Testing Notes
- Minimal test coverage currently (`tests/test_placeholder.py`)
- When adding tests, use `script/test` which activates venv automatically
- Integration tests require Home Assistant instance running

## Common Gotchas
- **Port conflicts**: Each instance needs unique port. Scripts auto-assign starting from 6053 (override with `LVAS_BASE_PORT` env var).
- **MAC spoofing**: Each instance needs unique MAC. Auto-generated if not specified in CLI config.
- **Preferences migration**: Legacy single-file preferences automatically split into per-instance + global files on first run. Old format had all settings in one JSON; new format separates CLI args (`{NAME}_cli.json`), active wake words (`{NAME}.json`), and shared HA settings (`ha_settings.json`).
- **Wake word stop model**: `stop.tflite` is special—not shown as selectable wake word in HA, used internally for ending conversations.
- **Systemd linger**: `script/deploy` enables user linger via `loginctl enable-linger` so services persist across SSH disconnects and reboots.
- **Development vs Production**: Use `script/run` for development (runs in foreground with live logs). Use `script/deploy` for production (installs as systemd service with autostart). Direct module execution (`python -m linux_voice_assistant`) requires manual venv activation and explicit args.
- **Legacy wrapper scripts**: Old deployments used manual `{NAME}_wrapper.py` scripts with hardcoded paths and MAC spoofing. Modern approach uses `script/deploy` which generates systemd units directly without wrapper intermediaries.
