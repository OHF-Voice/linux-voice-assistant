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

## Sherpa-onnx KWS Integration (added 2026-10-05)

### What was added
- **Third wake word engine**: sherpa-onnx KeywordSpotter (`WakeWordType.SHERPA_ONNX`, optional dep `[sherpa-kws]`).
- `linux_voice_assistant/sherpa_kws.py` - `SherpaKwsWakeWord` wrapper: raw 16 kHz s16le bytes in, `process_streaming() -> bool`; `sherpa_onnx` imported lazily inside methods (pure micro/oww setups never touch it).
- `linux_voice_assistant/wake_engines.py` - uniform adapters `MicroWakeWordAdapter` / `OpenWakeWordAdapter` over the two-stage micro/oww APIs (shared feature frontends).
- `models.py`: `AUDIO_SAMPLE_RATE = 16000` is the single sample-rate constant (recorder + sherpa `accept_waveform` both read it); `AvailableWakeWord.load()` is the single factory for all engines.

### Uniform engine contract
All three engines expose flat instance attributes `.wake_word`, `.id`, `.is_active`, settable `.probability_cutoff`, and `process_streaming(audio_chunk: bytes) -> bool`. No properties, no engine-specific branches in the audio loop.

### Ownership rules (learned the hard way - do not regress)
- JSON config parsing lives in ONE place: `_parse_wake_word_config()` in `wake_word.py` (used by `find_available_wake_words` with default cutoff 0.7 and `load_stop_model` with 0.5).
- sherpa path resolution lives in `SherpaKwsWakeWord.resolve_paths()` classmethod, called from `AvailableWakeWord.load()`; the constructor takes ready `model_files`.
- Stop model is separated from wake words purely by file stem vs `--stop-model` (default `stop`); exactly one stop model.
- External wake words (HA -> satellite) are micro/.tflite only per ESPHome protocol; sherpa is unreachable there by design.

### Import policy (decided with the developer)
- Our own modules: plain imports, no `TYPE_CHECKING`, no quoted annotations (style of `satellite.py`) - `wake_word.py` imports `sherpa_kws`/`wake_engines` directly.
- `TYPE_CHECKING` is allowed ONLY for optional third-party deps (`wake_engines.py`: pymicro_wakeword/pyopen_wakeword) - plain imports there would break pure-sherpa setups.
- CRITICAL: every annotation referencing a TYPE_CHECKING-only name MUST be quoted. Unquoted = runtime NameError at import. mypy does NOT catch this (it skips the check); only a real import in an env without the package does. `wake_engines.py` had 6 such latent NameErrors, fixed by quoting.

### Pitfalls (encoding / tooling on Windows desktop)
- NEVER rewrite whole files through PowerShell pipelines without `-Encoding UTF8` (PS 5.1 default is Windows-1251; historically corrupted Cyrillic comments, added BOMs, broke line endings making `git diff` show 800+ noise lines). Use targeted `edit_block` edits only.
- `git stash`/`pop` mid-edit rolls back unsaved edits - re-verify recent changes after any stash.
- `mypy.ini`: upstream fixed the broken `python_version >= 3.11` line to `python_version = 3.11` (their choice; keep it on syncs even though the local `lva` env is 3.13).

