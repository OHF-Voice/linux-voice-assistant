# Project Context for AI Agents

## Behaviour

- NEVER automatically reply on Github (PRs or Discussions) without explicit consent from the developer.
- Always run `./script/lint` and `./script/tests` after code changes to ensure standards are met.

- `docs/behavior.md` is the source of truth for user-visible behavior, and `docs/peripheral_api/v1.md` / `v2.md` for what LVA does for peripherals (per API version). Follow them when changing code. If the code contradicts it, ask which one is wrong instead of silently picking one.
- When a change alters user-visible behavior, update `docs/behavior.md` in the same change and list every behavior change separately under "Behavior changes" in the PR description (see `.github/PULL_REQUEST_TEMPLATE.md`).

### End-to-end tests (`tests/e2e/`)

The end-to-end tests pin down the behavior users rely on. An AI agent must not weaken them to make a change pass.

- **No change without explicit confirmation.** An AI agent never changes anything under `tests/e2e/` (tests, fixtures, the harness) or the end-to-end workflow on its own. It first tells the user what it wants to change and why, and waits for the user's explicit confirmation in the chat. This includes adding, removing or loosening assertions, changing timeouts or thresholds, and skipping or deleting a test. Confirmation for one change does not cover the next one.
- **Every change is checked against `docs/behavior.md`.** Before proposing a change to an end-to-end test, the agent checks it against `docs/behavior.md` and states the result. A failing end-to-end test is never fixed by adapting the test, unless `docs/behavior.md` says the new behavior is the right one. If the test and `docs/behavior.md` disagree, ask which one is wrong, do not pick one.
- **Every change is logged in the PR.** The PR description states in the section "End-to-end test changes" of `.github/PULL_REQUEST_TEMPLATE.md` whether the end-to-end tests were changed. If they were, it lists each change on its own line: which file or test, what was changed and why. Every PR fills this section, also when nothing under `tests/e2e/` changed.
- **Behavior changes need the developers' approval in the PR.** A change that makes an end-to-end test expect different behavior is a behavior change. The AI prepares the request for the developers in the PR: it lists the change under "Behavior changes" in the PR description, with what changes, why and what `docs/behavior.md` says. The change is only allowed once the developers have approved it in the PR. The AI never treats its own review as that approval, and posting anything to GitHub still needs the explicit consent above.

## Project Overview

LVA always means **Linux Voice Assistant**, this project.

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
- `linux_voice_assistant/mpv_player.py` - MediaPlayer facade used by LVA (playlists, done callbacks), delegates to `player/libmpv.py`
- `linux_voice_assistant/player/libmpv.py` - libmpv wrapper: playback, thread-safe state, volume/ducking, end-of-file callbacks
- `linux_voice_assistant/entity.py` - Entity definitions (MediaPlayer, MuteSwitch, etc.)
- `linux_voice_assistant/api_server.py` - ESPHome API server 
- `linux_voice_assistant/peripheral_api/` - Peripheral WebSocket API server, one module per API version
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

- **Python**: 3.11, 3.12, 3.13 supported
- **Formatting**: Black (200 char line length, py312/py313 target)
- **Import Sorting**: isort with black profile
- **Type Checking**: mypy (non-strict, configured in `mypy.ini`, targets Python 3.11)
- **Linting**: pylint (many checks disabled in pyproject.toml for practical reasons)

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
