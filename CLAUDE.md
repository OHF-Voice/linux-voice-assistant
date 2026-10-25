@AGENTS.md

# AI Working Guide for Linux Voice Assistant

Dense codebase map for AI agents. `AGENTS.md` (imported above) holds the rules; this file holds the knowledge needed to change code without re-reading every module. When this file and the code disagree, the code wins. Fix this file in the same change.

## Fast commands

| Task | Command | Time |
|------|---------|------|
| All unit tests | `./script/tests` | ~2 s (386 tests) |
| One file / one test | `.venv/bin/python -m pytest tests/unit/test_satellite.py -k timer -q` | <1 s |
| All lint | `./script/lint` | ~15 s |
| Auto-format | `./script/lint_black --auto && ./script/lint_isort --auto` | |
| Run app | `./script/run --debug` (needs mic, PulseAudio/PipeWire, libmpv) | |

`.venv` already exists. Only run `./script/setup --dev` if it is missing, because the script deletes and recreates `.venv`.

## Runtime architecture

One process with three concurrent execution contexts. Most bugs come from crossing between them.

```
asyncio event loop (main thread)
 ├─ ESPHome TCP server :6053  → VoiceSatelliteProtocol (one instance per HA connection)
 ├─ Peripheral WebSocket :6055 → PeripheralAPIServer (JSON events/commands)
 └─ Zeroconf (_esphomelib._tcp)
audio thread (process_audio in __main__.py, daemon)
 └─ mic → int16 → WebRTC (opt.) → satellite.handle_audio() + wake/stop word detection
mpv threads (python-mpv event callbacks)
 └─ end-file → done_callback chains (TTS finished, timer loop, music idle)
```

Thread-safety rules:
- `APIServer.send_messages()` is safe from any thread. It uses `call_soon_threadsafe` when it is called off the loop thread.
- Peripheral events: always use `emit_event_sync()` (or `satellite._emit()`), never `await emit_event()` from outside the loop.
- `done_callback`s run on mpv threads. Keep them non-blocking. The timer loop runs `time.sleep(1.0)` inside one callback, which is fine there.

## Central object: `ServerState` (models.py)

- It is created once in `__main__.main()` and outlives every HA connection. `VoiceSatelliteProtocol` is constructed per TCP connection and sets `state.satellite = self`.
- `state.entities` persists across reconnects. **Entity `key` = `len(state.entities)` at creation time.** Never reorder or remove entities. Append only.
- `state.connections` lists all live protocol instances. Use `state.broadcast(msgs)` for asynchronous state changes so every client gets them. `satellite.send_messages` reaches only one client.
- `Preferences` is loaded with `Preferences.from_dict()`, which drops unknown keys and logs a warning. Adding a field with a default is safe. **Renaming a field silently loses the stored value**, because the old key is dropped and the new one falls back to its default. Migrate the value explicitly if a rename is unavoidable.
- Persist helpers: `save_preferences()`, `persist_volume()` (it also emits `volume_changed`/`volume_muted` to peripherals), `persist_mic_gain/noise/volume()`.
- `oww_*_probability_cutoff` fields are legacy and unused. The thresholds in use are `wake_word_1_threshold`, `wake_word_2_threshold` and `stop_word_threshold`.

## Module map

| File | Responsibility | Key facts |
|------|---------------|-----------|
| `__main__.py` | argparse, resolve net/MAC/name, load prefs + models, build `ServerState`, start servers, `process_audio()` loop | Creates a throwaway `VoiceSatelliteProtocol(state)` before binding to catch `__init__` errors early. Binds with 15 retries on EADDRINUSE. Waits `--peripheral-startup-wait` s before serving, so peripherals can register entities first. |
| `api_server.py` | Minimal plaintext ESPHome frame parser/writer (`asyncio.Protocol`) | Answers Hello/Auth/Ping/Disconnect itself. Everything else goes to `handle_message()`, which is a **generator** yielding responses. |
| `satellite.py` | `VoiceSatelliteProtocol(APIServer)`: entity creation, message routing, voice pipeline, timers, TTS, ducking, external wake word download | See "Voice pipeline state" below. |
| `entity.py` | ESPHome entities. Each implements `handle_message(msg)` and yields responses for `ListEntitiesRequest`, `SubscribeHomeAssistantStatesRequest`, and its own `*CommandRequest` filtered by `msg.key == self.key` | `MediaPlayerEntity`, `MuteSwitchEntity`, `ThinkingSoundEntity`, `MicSettingEntity` (Number, or Select if `options`), `WakeWord1/2SensitivityNumberEntity`, `StopWordSensitivityNumberEntity`, `LEDLightEntity`, `ButtonEventSensorEntity`, `ButtonLockEntity`. Old aliases are at the bottom. |
| `peripheral_api.py` | WebSocket server. `LVAEvent` (LVA→peripheral), `LVACommand` (peripheral→LVA), snapshot + replay of the last state event on connect | The module docstring is the protocol spec and must stay in sync with `docs/peripheral_api.md`. |
| `wake_word.py` | `find_available_wake_words()` (globs `*.json` top-level only), `load_wake_models()` (fallback chain: requested → `--wake-model` → `okay_nabu` → first available), `load_stop_model()` | openWakeWord ids are versioned (`hey_jarvis_v0.1`). Prefix matching handles them. |
| `models.py` | `WakeWordType`, `AvailableWakeWord` (`.load()`), `LightRegistration`, `Preferences`, `ServerState`, `initial_stop_word_threshold()` | |
| `mpv_player.py` | `MpvMediaPlayer`: playlist (list of URLs), done_callback, duck/unduck | `stop()` **invokes** the pending done_callback. |
| `player/libmpv.py` | `LibMpvPlayer`: real mpv wrapper, effective volume = user × duck factor | Only an `end-file` with reason 0 (EOF) fires the callback. `stop(for_replacement=True)` drops the callback. Many debug logs wrongly say "unduck() called", so don't rely on them. |
| `webrtc.py` | `WebRTCProcessor`: 10 ms (320 byte) framing for `webrtc_noise_gain` | Can return `b""` while buffering. The audio loop then skips the chunk. |
| `zeroconf.py` | mDNS registration | |
| `util.py` | `get_version()` (reads `version.txt`), `get_esphome_version()`, `call_all()`, interface/IP detection | |

Two mpv players share the volume: `state.tts_player` (announcements, chimes, TTS, timer) and `state.music_player` (media, can use `--music-output-device`). The pipeline ducks only `music_player`.

## Voice pipeline state (satellite.py)

Flags on the satellite instance: `_is_streaming_audio`, `_pipeline_active`, `_tts_url`, `_tts_played`, `_continue_conversation`, `_timer_finished`, `_timer_ring_start`, `_processing`.

Flow: `wakeup()` (audio thread) → blocked if `state.muted` or `_pipeline_active` → duck, play wake sound → on finish send `VoiceAssistantRequest(start=True)`, set `_is_streaming_audio=True`, emit `listening` → HA events in `handle_voice_event()`:
- `RUN_START`: store tts url, `_pipeline_active=True`
- `STT_VAD_END`/`STT_END`: stop streaming. `STT_END` emits `stt_text`.
- `INTENT_START`: emit `thinking`, optional thinking sound
- `INTENT_PROGRESS` with `tts_start_streaming=1`: `play_tts()` early
- `INTENT_END` with `continue_conversation=1`: remember it
- `TTS_END`: `play_tts()`, which is idempotent via `_tts_played`
- `RUN_END`: if no TTS was played, call `_tts_finished()` now. Otherwise `_pipeline_active` stays True until playback ends, so speaker audio can't trigger the wake word.

`_tts_finished()` sends `VoiceAssistantAnnounceFinished`, removes the stop word from the active set, then either restarts listening after `continue_conversation_delay` (via `threading.Timer`) or unducks and emits `idle`.

**Stop word gating:** the stop word only triggers when `state.stop_word.id in state.active_wake_words`. It is added during TTS, announcements and timer ringing, and removed in `stop()` / `_tts_finished()` / timer stop. Forgetting the removal leaves "stop" live forever.

Timers: `FINISHED` starts the `_play_timer_finished()` loop, which re-plays the sound with a 1 s gap and auto-stops after `--timer-max-ring-seconds`. `CANCELLED` emits `timer_updated` with `seconds_left: 0` and then `idle`.

Wake word slots: HA can activate up to 2 wake words. `preferences.active_wake_words` is a 2-slot list (it may contain `None`), and words keep their slot when the set changes. Sensitivity entity 1/2 map to slot 0/1. The audio thread reloads its model list when `state.wake_words_changed` is True.

`connection_lost()` stops playback only when the **last** client disconnects.

## Recurring patterns

**Satellite entity "get-or-create + reattach"** (satellite `__init__`): reuse the existing entity from `state`, re-append it if it's missing, then always reassign `entity.server = self` and refresh the callbacks (`update_get_*`/`update_set_*`), and call `sync_with_state()`. Follow the same shape for new built-in entities.

**Peripheral opt-in entities** (Light, Button, ButtonLock): a peripheral sends `register_*` → `PeripheralAPIServer._register_*` sets `state.pending_*` → `satellite.register_pending_*()` materialises it idempotently (also called from satellite `__init__` on reconnect) → `_schedule_ha_reconnect_for_late_entity()` closes the HA transport once (1.5 s debounce, 60 s cooldown) so HA re-enumerates entities.

**HA quirk:** `ButtonEventSensorEntity` must not yield an `EventResponse` with an empty `event_type`. HA then fails the whole config entry.

**Feature detection on aioesphomeapi:** `_HAS_AUDIO_DATA2` checks the protobuf descriptor. Do this for newer proto fields, because `aioesphomeapi` is pinned (`==45.3.1`).

## Change checklists

**New CLI option**
1. `__main__.py` argparse. If the app needs it at runtime, add a `ServerState` field with a default (otherwise `make_state()` in conftest has to handle it).
2. `docker-entrypoint.sh` env → flag mapping.
3. `.env.example`.
4. `docs/install_application.md`: systemd `Environment=` block and the env var reference table.
5. `README.md` parameter table.

Existing env quirk: `REFACTORY_SECONDS` is misspelled on purpose (it's the public name). Don't "fix" it.

**New peripheral event or command**
1. Add a member to the `LVAEvent`/`LVACommand` enum and a branch in `_dispatch_command`.
2. Update the module docstring in `peripheral_api.py` and `docs/peripheral_api.md`.
3. If it represents an ongoing visual state, add it to `_STATE_EVENTS` in `emit_event()` (it's replayed on connect).
4. Maybe update `examples/*` peripherals and `examples/Peripheral web console/`, since #423 touched all of them.
5. Tests in `tests/unit/test_peripheral_api.py` (`dispatch()` helper).

**New HA-visible entity**
1. Class in `entity.py` (+ `__all__`).
2. Optional slot on `ServerState`.
3. Get-or-create block in satellite `__init__`.
4. Persist through `Preferences` if needed.
5. Document it in `docs/configuration.md`.

**New preference**: add the field with a default to `Preferences`, read it in `main()` before `ServerState` is built, and write it through a `persist_*`/setter that calls `save_preferences()`.

## Tests

- `tests/unit/conftest.py`: `make_state(tmp_path, **overrides)` auto-fills required `ServerState` fields. `make_satellite(tmp_path, state_overrides)` patches the sensitivity entities and sets `satellite._writelines = MagicMock()` so sent frames can be inspected.
- The players, stop word and peripheral API are MagicMocks. Assert on calls like `state.tts_player.play.assert_called_with(...)`.
- `asyncio_mode = "auto"`, so `async def test_*` works without a decorator.
- One test file per module (`test_unit.py` covers `util.py`).
- Pylint for tests uses `tests/.pylintrc`.

## Gotchas

- `ServerState.audio_input_channels` defaults to 2, but argparse (default **1**) always overrides it. The effective default is mono.
- In Docker the working directory is `/app`, so the default `--download-dir` is `/app/local` (a named volume in `docker-compose.yml`). Bare metal uses `local/` inside the repo.
- Paths in `.env.example` and the docs are written for the container (`/app/...`). The systemd examples use `/home/pi/linux-voice-assistant/...`.
- `mypy` runs non-strict with `python_version = 3.11` (`mypy.ini`). Don't use syntax newer than 3.11.
- Some older docstrings in modules not touched recently may still be Google style. New code must use the Sphinx `:param:` style from AGENTS.md.

## Documentation map

| Doc | Contents |
|-----|----------|
| `README.md` | Features, hardware, CLI parameter table, dev setup |
| `docs/install.md` → `install_audioserver.md`, `install_application.md` | Install flow (PipeWire/PulseAudio, Docker Compose, systemd, env var reference, wake word lists) |
| `docs/behavior.md` | User-visible behavior (phases, ducking, stop word, timers, announcements). Binding for code changes, see AGENTS.md |
| `docs/configuration.md` | Every HA device-page entity, from the user's view |
| `docs/peripheral_api.md` | Full WebSocket protocol for peripheral authors |
| `docs/enabling_aec.md` | Echo cancellation via PulseAudio/PipeWire |
| `docs/debugging.md` | Audio stack / container socket troubleshooting |
| `examples/*/` | Peripheral containers (Satellite1, ReSpeaker 2/4-mic, USB array, Jabra, web console) using the WebSocket API |
| `.github/workflow.md` | Docker build workflows (release/nightly/PR) |
| `.kilo/skill/review-pr/` | PR review procedure + `REVIEW_STANDARDS.md` (severity tags, no conventional-commit prefixes in PR titles) |

## Contribution constraints

- PR titles describe the change functionally, with **no** `feat:`/`fix:` prefixes. Labels come from the ticked box in `.github/PULL_REQUEST_TEMPLATE.md`.
- `.github/AI_POLICY.md`: no autonomous contributions, a human must review everything, and nothing is posted to GitHub without explicit consent.
- `CHANGELOG.md` is generated from release notes. Don't edit it by hand.
- AGENTS.md mentions a `stable` branch for backports, but `origin` currently has none. Check `git branch -r` before suggesting `backport-to-stable`.
