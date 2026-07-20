# Linux Voice Assistant - Copilot Instructions

## Project Overview

**Linux Voice Assistant (LVA)** - A Linux-based voice satellite software for Home Assistant using the ESPHome protocol.

### Architecture
- Uses `aioesphomeapi` for ESPHome protocol communication
- Supports OpenWakeWord and MicroWakeWord for wake word detection
- Uses `soundcard` library for audio input, `python-mpv` for output
- PulseAudio/PipeWire audio support

### Key Components
- `linux_voice_assistant/satellite.py` - VoiceSatelliteProtocol
- `linux_voice_assistant/wake_word.py` - Wake word detection
- `linux_voice_assistant/webrtc.py` - Noise suppression/gain
- `linux_voice_assistant/mpv_player.py` - Audio output

## Development Commands

```bash
./script/setup --dev    # Install dev dependencies
./script/lint           # Run all linting checks
./script/tests          # Run pytest unit tests
```

## Code Style

- **Formatting**: Black (200 char line length)
- **Imports**: isort with black profile
- **Types**: mypy strict mode
- **Lint**: pylint (many checks disabled in pyproject.toml)

## Pull Request Guidelines

- One feature/fix per PR
- Run `./script/lint --auto` and `./script/tests` before submitting
- Add tests for new functionality in `tests/unit/`
- Update documentation for user-facing changes

## Verification Checklist

Before claiming completion:
1. Run `./script/lint` - all checks must pass
2. Run `./script/tests` - all tests must pass
3. If touching audio code, note that hardware testing is required but not performed in tests
4. Verify changes work with Python 3.11, 3.12, and 3.13
