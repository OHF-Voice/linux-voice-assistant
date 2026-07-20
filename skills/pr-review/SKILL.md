# PR Review Workflow for Linux Voice Assistant

## Review Procedure

When reviewing a PR, follow this checklist:

### 1. Understanding the Change
- Identify the files modified and their purpose
- Check if changes align with project architecture
- Verify no unrelated rewrites are suggested

### 2. Bug Detection
- Check for logic errors in `satellite.py`, `wake_word.py`, `webrtc.py`
- Verify type safety (mypy compliance)
- Check for proper error handling

### 3. Testing
- Verify tests exist for changed code
- Check that `./script/tests` passes
- Note if hardware-dependent code lacks simulation

### 4. Areas Requiring Extra Care
- `linux_voice_assistant/wake_word.py` - hardware-dependent
- `linux_voice_assistant/mpv_player.py` - requires libmpv-dev
- `linux_voice_assistant/webrtc.py` - noise suppression algorithms
- `linux_voice_assistant/satellite.py` - ESPHome API protocol

### 5. Output Format
Keep summary concise, cite file:line numbers, mention test coverage.