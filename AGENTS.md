# Project Context for AI Agents

## Behaviour

- NEVER automatically reply on Github (PRs or Discussions) without explicit consent from the developer.
- Always run `./script/lint` and `./script/tests` after code changes to ensure standards are met.

## Project Overview

**Linux Voice Assistant (LVA)** - A Linux-based voice satellite software for Home Assistant using the ESPHome protocol.

### Purpose
- Provides voice control and interaction for Home Assistant via the Assist integration
- Runs on x64/ARM64 Linux hardware with PulseAudio/PipeWire audio support
- Performs local wake word detection (OpenWakeWord/MicroWakeWord) and on-device processing

### Architecture
- **Protocol**: ESPHome API via `aioesphomeapi` for Home Assistant integration
- **Wake Words**: Supports both OpenWakeWord and MicroWakeWord engines
- **Audio**: Uses `soundcard` library for input, `python-mpv` for output
- **Peripherals**: WebSocket API for LEDs, buttons, HAT boards
- **Discovery**: mDNS/ZeroConf for auto-discovery by Home Assistant

### Key Components
- `linux_voice_assistant/__main__.py` - Main entry point with CLI argument parsing
- `linux_voice_assistant/satellite.py` - VoiceSatelliteProtocol implementation (ESPHome API)
- `linux_voice_assistant/wake_word.py` - Wake word model loading and detection
- `linux_voice_assistant/webrtc.py` - WebRTC noise gain/NS processing
- `linux_voice_assistant/mpv_player.py` - MPV-based media player
- `linux_voice_assistant/entity.py` - Entity definitions (MediaPlayer, MuteSwitch, etc.)
- `linux_voice_assistant/api_server.py` - Peripheral WebSocket API server
- `linux_voice_assistant/models.py` - Preferences and ServerState dataclasses

## Development Commands

### Setup
```bash
./script/setup --dev    # Install dev dependencies in virtual environment
```

### Linting
```bash
prek run lint --all-files # Run all linting checks (has to be done inside the dev venv)
./script/lint_ruff     # Run Ruff
./script/lint_mypy   # pylint code quality checks
```

### Testing
```bash
prek run tests --all-files # Run pytest unit tests (has to be done inside the dev venv)
```

## Code Quality Standards

- **Python**: 3.11, 3.12, 3.13 supported
- **Formatting**: BRuff
- **Import Sorting**: Ruff
- **Type Checking**: mypy with strict settings
- **Linting**: Ruff (many checks disabled in pyproject.toml for practical reasons)

## Testing Strategy

- Unit tests in `tests/unit/` using pytest with asyncio mode
- Test helper `make_state()` in `tests/unit/conftest.py` for creating state fixtures
- Mocks used heavily for hardware dependencies (audio devices, MPV, wake words)

## CI/CD

- GitHub Actions for lint checks and unit tests
- Tests run on Python 3.12 with libmpv-dev system dependency
- Lint runs on Python 3.13

## Branching and PRs

- All PRs target `main` (primary development branch). `stable` is for production releases.
- PRs labeled `bugfix` + `backport-to-stable` are automatically backported to `stable` — use only for bugs also present in `stable`.

## Agent Commands

When making code changes, run these commands in order:

1. **Format code and Lint**: `prek run lint --all-files`(inside the dev venv)
2. **Run tests**: `prek run tests --all-files` (inside the dev venv)

## Code Style

### Comments

Only use comments to explain complex, multi-line blocks of code. Do not comment obvious operations. Inline comments are there to explain code parts that need explaining - keep them when editing code.

### Docstring Format

Use Sphinx-style docstrings with `:param:` syntax. For simple functions, a single-line docstring is fine. The docstring should provide clarity to the caller, not explain how it works technically.

```python
def my_function(param1: str, param2: int, param3: bool = False) -> str:
    """
    Brief one-line description of the function.
    :param param1: Description of what param1 is used for.
    :param param2: Description of what param2 is used for.
    :param param3: Description of what param3 is used for.
    """
```

Do **not** use Google-style (`Args:`) or bullet-style (`- param:`) docstrings.

### File structure

Private methods should be at the bottom of the file, public at the top.

## Verification Checklist

Before claiming completion:
- [ ] Ran `prek run lint --all-files` - all checks passed
- [ ] Ran `./script/tests` - all tests passed
- [ ] For audio-related changes: Note that hardware testing (microphone/speaker) was NOT performed
- [ ] Did NOT claim hardware behavior is verified unless actually exercised
- [ ] Changes are focused on one feature or fix per change

## Areas Requiring Extra Care

- `linux_voice_assistant/wake_word.py` - Wake word model loading is hardware-dependent
- `linux_voice_assistant/mpv_player.py` - Audio output requires `libmpv-dev`
- `linux_voice_assistant/webrtc.py` - Noise suppression/gain algorithms
- `linux_voice_assistant/satellite.py` - ESPHome API protocol handling