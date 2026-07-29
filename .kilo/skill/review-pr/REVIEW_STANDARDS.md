# PR Review Standards

## Review Philosophy

- Only comment when confident an issue exists
- Be concise: one sentence per comment when possible
- Focus on actionable feedback, not observations
- When reviewing text, only comment on clarity issues if the text is genuinely confusing or could lead to errors

## What to Analyze

Review all code changes for:

- Code quality and style consistency with the existing codebase
- Potential bugs or issues
- Performance implications
- Blocking IO in async code
- Security concerns
- Test coverage
- Documentation updates if needed

## Project Standards

Respect the project standards as outlined in AGENTS.md. Any deviations must be raised as `[PROBLEM]`.

Key standards for LVA:
- Python 3.11, 3.12, 3.13 supported
- Black formatting with 200 char line length
- isort import sorting with black profile
- mypy type checking with strict settings
- pylint code quality checks (many checks disabled in pyproject.toml)

## Architecture-Specific Checks

- `linux_voice_assistant/wake_word.py` - Wake word model loading is hardware-dependent, verify no assumptions about specific hardware
- `linux_voice_assistant/mpv_player.py` - Audio output requires `libmpv-dev`, check for proper error handling
- `linux_voice_assistant/webrtc.py` - Noise suppression/gain algorithms need careful review for audio quality
- `linux_voice_assistant/satellite.py` - ESPHome API protocol handling must be robust

## CI Context

Lint runs on Python 3.13, tests run on Python 3.12 with libmpv-dev.

### What CI Checks

- **Lint**: `.github/workflows/lint.yml` runs all linting checks
- **Tests**: `.github/workflows/tests.yml` runs pytest unit tests

## Skip These (Low Value)

Do not comment on:

- Style/formatting (pre-commit handles this)
- Test failures (CI catches this)
- Missing dependencies (CI catches this)
- Minor naming suggestions
- Suggestions to add comments
- Logging suggestions unless security-related

## Issue Categories

Categorize every issue as:

- `[CRITICAL]` — must fix before merging (bugs, security issues, broken functionality)
- `[PROBLEM]` — should fix (code quality, bad patterns, missing tests)
- `[SUGGESTION]` — optional improvement (style, minor refactors, nice-to-haves)

## PR Title

The PR title must be a functional description of the change. It must NOT contain conventional commit prefixes such as `feat:`, `fix:`, `refactor:`, `chore:`, etc. Labels categorize PRs, not the title. Flag as `[PROBLEM]` if the title uses such prefixes.