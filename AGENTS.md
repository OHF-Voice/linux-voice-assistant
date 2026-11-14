# Project Context for AI Agents

## Behaviour

- NEVER automatically reply on Github (PRs or Discussions) without explicit consent from the developer.
- Always run `./script/lint` and `./script/tests` after code changes to ensure standards are met.

- `docs/behavior.md` is the source of truth for user-visible behavior. Follow it when changing code. If the code contradicts it, ask which one is wrong instead of silently picking one.
- When a change alters user-visible behavior, update `docs/behavior.md` in the same change and list every behavior change separately under "Behavior changes" in the PR description (see `.github/PULL_REQUEST_TEMPLATE.md`).

## Project Overview

LVA always means **Linux Voice Assistant**, this project.

**Linux Voice Assistant (LVA)** - A Linux-based voice satellite software for Home Assistant using the ESPHome protocol.

### Purpose
- Provides voice control and interaction for Home Assistant via the Assist integration
- Runs on **32-bit ARM** (linux/arm/v7 armhf) Linux hardware — Raspberry Pi 2/3 (32-bit OS), Orange Pi, Allwinner H3/H5 boards
- Performs local wake word detection using **MicroWakeWord only** and on-device processing

### Architecture
- **Protocol**: ESPHome API via `aioesphomeapi` for Home Assistant integration
- **Wake Words**: Supports MicroWakeWord only (TFLite-based; OpenWakeWord requires TensorFlow which has no 32-bit ARM wheels)
- **Audio**: Uses `soundcard` library for input, `python-mpv` for output
- **Peripherals**: WebSocket API for LEDs, buttons, HAT boards
- **Discovery**: mDNS/ZeroConf for auto-discovery by Home Assistant

### Key Components
- `linux_voice_assistant/__main__.py` - Main entry point with CLI argument parsing
- `linux_voice_assistant/satellite.py` - VoiceSatelliteProtocol implementation (ESPHome API)
- `linux_voice_assistant/wake_word.py` - Wake word model loading and detection
- `linux_voice_assistant/webrtc.py` - WebRTC noise gain/NS processing
- `linux_voice_assistant/mpv_player.py` - MediaPlayer facade used by LVA (playlists, done callbacks), delegates to `player/libmpv.py`
- `linux_voice_assistant/player/libmpv.py` - libmpv wrapper: playback, thread-safe state, volume/ducking, end-of-file callbacks
- `linux_voice_assistant/entity.py` - Entity definitions (MediaPlayer, MuteSwitch, etc.)
- `linux_voice_assistant/api_server.py` - ESPHome API server 
- `linux_voice_assistant/peripheral_api.py` - Peripheral WebSocket API server
- `linux_voice_assistant/models.py` - Preferences and ServerState dataclasses

## Development Commands

### Setup
```bash
./script/setup --dev    # Install dev dependencies in virtual environment
```

### Linting
```bash
./script/lint           # Run all linting checks
./script/lint_black     # Black formatting check (add --auto to fix)
./script/lint_isort     # isort import sorting check (add --auto to fix)
./script/lint_flake8    # flake8 style checks
./script/lint_mypy      # mypy type checking
./script/lint_pylint    # pylint code quality checks
```

### Testing
```bash
./script/tests          # Run pytest unit tests
```

## Code Quality Standards

- **Python**: 3.11 only (this branch; no 32-bit ARM wheels exist for 3.12/3.13 dependencies)
- **Formatting**: Black (200 char line length, py311 target)
- **Import Sorting**: isort with black profile
- **Type Checking**: mypy (non-strict, configured in `mypy.ini`, targets Python 3.11)
- **Linting**: pylint (many checks disabled in pyproject.toml for practical reasons)

## Testing Strategy

- Unit tests in `tests/unit/` using pytest with asyncio mode
- Test helper `make_state()` in `tests/unit/conftest.py` for creating state fixtures
- Mocks used heavily for hardware dependencies (audio devices, MPV, wake words)

## CI/CD

- GitHub Actions run lint and unit tests on Python 3.11, and build the `linux/arm/v7` Docker image from `Dockerfile` under QEMU (see `.github/workflow.md`).
- Images are published to ghcr.io with an `armhf` marker in every tag: `<version>-armhf` / `armhf` (release), `nightly-armhf` (push to `arm32`), `<pr-number>-armhf` (PRs).
- Lint and tests run on the x86-64 runner. They do not need armhf hardware.

## Branching and PRs

- This is the `arm32` branch — a 32-bit ARM fork of the upstream `main` branch.
- PRs against this branch should target `arm32`, not `main`.
- For upstream (x86-64 / arm64) changes, see the [OHF-Voice/linux-voice-assistant](https://github.com/OHF-Voice/linux-voice-assistant) repository.

## Agent Commands

When making code changes, run these commands in order:

1. **Format code**: `./script/lint_black --auto` and `./script/lint_isort --auto`
2. **Run linting**: `./script/lint`
3. **Run tests**: `./script/tests`

## Code Style

### Language

All documentation (`docs/`, `README.md`, `*.md`), code comments, docstrings and commit/PR text are written in English, regardless of the language used in the conversation with the developer.

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
- [ ] Ran `./script/lint` - all checks passed
- [ ] Ran `./script/tests` - all tests passed
- [ ] For audio-related changes: Note that hardware testing (microphone/speaker) was NOT performed
- [ ] Did NOT claim hardware behavior is verified unless actually exercised
- [ ] Changes are focused on one feature or fix per change
- [ ] `docs/behavior.md` still matches the code, and behavior changes are listed separately in the PR description

## Areas Requiring Extra Care

- `linux_voice_assistant/wake_word.py` - Wake word model loading is hardware-dependent
- `linux_voice_assistant/mpv_player.py` and `linux_voice_assistant/player/libmpv.py` - Audio output requires `libmpv-dev`
- `linux_voice_assistant/webrtc.py` - Noise suppression/gain algorithms
- `linux_voice_assistant/satellite.py` - ESPHome API protocol handling
