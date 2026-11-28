@AGENTS.md

# AI Working Guide for Linux Voice Assistant

Dense codebase map for AI agents. `AGENTS.md` (imported above) holds the rules; this file holds the knowledge needed to change code without re-reading every module. When this file and the code disagree, the code wins. Fix this file in the same change.

## Fast commands

| Task | Command | Time |
|------|---------|------|
| All unit tests | `./script/tests` | ~13 s (920 tests) |
| End-to-end tests (real LVA, virtual mic/speaker, fake HA; needs PulseAudio, see `tests/e2e/README.md`) | `./script/e2e` (LVA tests: `--ignore=tests/e2e/peripheral_api`, peripheral API tests: `tests/e2e/peripheral_api`) | ~4.5 min (41 tests: 23 LVA, 18 peripheral API) |
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
 └─ end-file → done_callback chains (TTS finished, timer sound repeat, music idle)
```

Thread-safety rules:
- `APIServer.send_messages()` is safe from any thread. It uses `call_soon_threadsafe` when it is called off the loop thread.
- Peripheral events: always use `emit_event_sync()` (or `satellite._emit()`), never `await emit_event()` from outside the loop.
- `done_callback`s run on mpv threads, or on the thread that calls `MpvMediaPlayer.stop()`, which invokes the pending callback synchronously. Keep them non-blocking: the timer sound schedules its 1 s pause with `threading.Timer`.

## Central object: `ServerState` (models.py)

- It is created once in `__main__.main()` and outlives every HA connection. `VoiceSatelliteProtocol` is constructed per TCP connection and sets `state.satellite = self`.
- `state.entities` persists across reconnects. **Entity `key` = `len(state.entities)` at creation time.** Never reorder or remove entities. Append only.
- `state.connections` lists all live protocol instances. Use `state.broadcast(msgs)` for asynchronous state changes so every client gets them. `satellite.send_messages` reaches only one client.
- `Preferences` is loaded with `Preferences.from_dict()`, which drops unknown keys and logs a warning. Adding a field with a default is safe. **Renaming a field silently loses the stored value**, because the old key is dropped and the new one falls back to its default. Migrate the value explicitly if a rename is unavoidable.
- Persist helpers: `state.preferences.save(state.preferences_path)`, `persist_volume()` (it also emits `volume_changed`/`volume_muted` to peripherals), `persist_mic_gain/noise/volume()`.
- `oww_*_probability_cutoff` fields are legacy and unused. The thresholds in use are `wake_word_1_threshold`, `wake_word_2_threshold` and `stop_word_threshold`.

## Module map

| File | Responsibility | Key facts |
|------|---------------|-----------|
| `__main__.py` | argparse, resolve net/MAC/name, load prefs + models, build `ServerState`, start servers, `process_audio()` loop | Creates a throwaway `VoiceSatelliteProtocol(state)` before binding to catch `__init__` errors early. Binds with 15 retries on EADDRINUSE. Waits `--peripheral-startup-wait` s before serving, so peripherals can register entities first. |
| `api_server.py` | Minimal plaintext ESPHome frame parser/writer (`asyncio.Protocol`) | Answers Hello/Auth/Ping/Disconnect itself. Everything else goes to `handle_message()`, which is a **generator** yielding responses. |
| `satellite.py` | `VoiceSatelliteProtocol(APIServer)`: entity creation, message routing, voice pipeline, timer events (passed to `state.timer_alarm`), TTS, ducking, starting the external wake word download (`_set_active_wake_words()`) | See "Voice pipeline state" below. |
| `entity.py` | ESPHome entities. Each implements `handle_message(msg)` and yields responses for `ListEntitiesRequest`, `SubscribeHomeAssistantStatesRequest`, and its own `*CommandRequest` filtered by `msg.key == self.key` | `MediaPlayerEntity`, `MuteSwitchEntity` (a read-only binary sensor instead of a switch with `--enable-remote-unmute 0`), `ThinkingSoundEntity`, `MicSettingEntity` (Number, or Select if `options`), `WakeWord1/2SensitivityNumberEntity`, `StopWordSensitivityNumberEntity`, `LEDLightEntity`, `ButtonEventSensorEntity`, `ButtonLockEntity`. Old aliases are at the bottom. |
| `peripheral_api/` | Versioned WebSocket API, one folder per version: `server.py` (bind, routing by URL path, emits to every version), `versions.py` (`resolve_version()`, handler protocol), `v1/api.py` (frozen, `PeripheralApiV1`), `v2/api.py` (`PeripheralApiV2`, reduced status API; works its status out from `ServerState` after every event, with the pure logic in `v2/status.py` and `v2/timers.py`; it keeps no timers of its own and summarizes `state.timers` on `state.timer_alarm.now()`). In version 2 a message is an `event` when it reports something and a `command` when it asks for something, whatever the direction (the peripheral reports the hardware mute with events, LVA asks to switch it with commands). State changes without an event (media player, media announcement) call `PeripheralAPIServer.refresh_status_sync()`. `LVAEvent` (LVA→peripheral), `LVACommand` (peripheral→LVA), snapshot + replay of the last state event on connect | The module docstring is the protocol spec and must stay in sync with `docs/peripheral_api/readme.md`. |
| `wake_word.py` | `wake_word_display_name()` (id of a wake word: origin - engine - phrase, e.g. `Internal - MWW - Hey Jarvis`; stored, sent to peripherals and as `id` to HA. The name HA shows comes from `wake_word_names.py`), `find_matching_wake_word_id()` (id or file name to id, custom wins), `wake_word_ids_for_selections()` / `wake_word_selections_for_ids()` (preferences hold `WakeWordSelection(origin, engine, file_id)`, runtime uses the name), `find_available_wake_words()` (globs `*.json` top-level only, stores the parent directory name as `origin`), `load_wake_models()` (fallback chain: requested → `--wake-model` → `okay_nabu` → first available), `load_stop_model()` | openWakeWord ids are versioned (`hey_jarvis_v0.1`). Prefix matching handles them. |
| `wake_word_download.py` | Wake words Home Assistant offers for download (its `custom_wake_words/` folder, microWakeWord only): `wake_words_to_download()` (pure), `describe_external_wake_word()` (log text), `download_external_wake_word()` (blocking, timeout 30 s, writes `<file>.part` then renames, returns None on failure) | The satellite runs the download with `asyncio.to_thread` in a task kept in `state.wake_word_download`; a newer `VoiceAssistantSetConfiguration`, also from a new connection, cancels it. The selection is activated after the download. |
| `wake_word_audio.py` | Wake word verification by Home Assistant (#509): `is_wake_word_audio_requested()` (subscription flag), `buffer_wake_word_audio()` (both channels, at least 1.5 s, empty while paused), `take_wake_word_audio()` (last 1.5 s per channel, logs size and level), `start_request()`, `audio_messages()` (both channels in every message, never an empty one), `audio_level()` | Pure. The buffer lives in `process_audio()`, which passes it to `wakeup()`. aioesphomeapi forwards the audio fields only if `wake_word_id` is set. |
| `wake_word_detection.py` | Slot-ordered active wake words, per-slot thresholds, `gate_wake_words()` (pause while a pipeline runs, reset on resume), `process_wake_words()` (scoring, mute, refractory) | Pure functions, called once from `process_audio()`. Slot comes from `preferences.active_wake_words`, not from dict order. |
| `wake_word_names.py` | Names in HA (issue #641): `wake_word_name()` (MWW = phrase, OWW = phrase + ` (OWW)`), `name_for_wake_word_id()` (the `wake_word_phrase` sent on a detection), `offered_wake_words()` (one wake word per name: custom > downloaded > internal, MWW wins a collision with an OWW name and logs an error; HA offers as `Download from HA - <phrase>` unless the file id is already in `downloaded`), `replace_hidden_wake_words()` (called in `main()` before the models load) | Pure, logs every decision. The id never changes, only the name. |
| `wake_word_reload.py` | Makes HA read the wake word list again after a rename: `WakeWordListReload` (`NONE` → `NAMES_CHANGED` → `RESTORE_SELECTION` → `NONE`), `handshake_action()`, `reported_active_wake_words()` | Step in `state.wake_word_list_reload`, starts at `NAMES_CHANGED` when `Preferences.wake_word_names_changed` (set by `Migration644`, cleared after the restore). Set after a download too. The satellite reconnects with `_reconnect_home_assistant()` (1 s, `close_connection()` on every client); those connections keep the music playing in `connection_lost()`. |
| `migrations.py` | `migrate_preferences()`: one-time migrations of `preferences.json`, run in `main()` before the wake words load. Each migration is a class named after the PR that introduced it (`Migration591`, with `id` and `migrate()`), added to `_MIGRATIONS`, and its `id` is recorded in `Preferences.applied_migrations` | Returns new preferences, the caller saves them. |
| `stop_word.py` | `process_stop_word()`: stop model runs only while its id is in `active_wake_words`, reset on activation | Same gating pattern as wake words. |
| `timers.py` | LVA's own timer list (issue #590): `TimerStatus` (spec values of the HA `timer_list`), `Timer` (frozen), `TimerEvent`, pure functions `apply_timer_event()`, `due_timers()`, `start_ringing()`, `stop_ringing()`, `auto_stopped_timers()`, `restart_ringing()`, `merge_timers()`, `cleanup_timers()`, `display_timer()`, `timers_to_dicts()`/`timers_from_dicts()`. `TimerList` holds the timers (thread-safe, mutators return True on change) | Lives in `state.timers`, stored in `Preferences.timers` through `state.persist_timers()`. The caller passes `now` and does ringing, storing and sending. |
| `home_assistant_time.py` | `clock_offset()` (HA time minus own clock from `GetTimeResponse.epoch_seconds`, +0.5 s for the cut-off), `check_clock_offset()` (error log above 10 s), `request_time_periodically()` (sends `GetTimeRequest` at once, then every 30 s) | The satellite starts the requests after `AuthenticationRequest` (task `_time_requests`, cancelled in `connection_lost`) and stores the result in `state.home_assistant_time_offset` (not persisted). `TimerAlarm` uses own clock + offset. |
| `home_assistant_timers.py` | `get_timers_request()` (`timer_list.get_timers` with `wants_response`), `parse_timer_list()` / `timer_from_item()` (tolerant: times as ISO text or epoch, durations as seconds, `HH:MM:SS` or dict; items found wherever nested), `TimerSync` (call ids, ignores stale answers, `give_up()` after 10 s, hint logged once, `known_timer_ids` for the cleanup) | The satellite sends the request on `SubscribeHomeassistantServicesRequest` and passes `HomeassistantActionResponse` on; `TimerAlarm.merge()` merges it. Assumed: HA answers a device with its own timers only. The answer format is not fixed in the spec yet. The e2e fake HA does not subscribe to actions, so this path has no e2e coverage. |
| `timer_alarm.py` | `TimerAlarm`: carries out the timer list. `handle_event()` (HA timer events), `tick()` (once a second from `run_countdown()`, an asyncio task started in `main()` after the peripheral startup wait), `stop_ringing(report_idle=True)` (stop word, wake word, buttons, peripheral commands; stops all), `restore()` (ringing of a previous run), `merge()` (timer list of HA) | Lives in `state.timer_alarm` (created in `ServerState.__post_init__`), so ringing outlives the HA connection. Plays the timer sound on `tts_player` with a 1 s pause, ducks with owner `timer`, adds/removes the stop word, emits the version 1 timer events per timer. A sound round counter stops pending repeats of an earlier ringing. |
| `models.py` | `WakeWordType`, `AvailableWakeWord` (`.load()`), `LightRegistration`, `Preferences`, `ServerState`, `initial_stop_word_threshold()` | |
| `mpv_player.py` | `MpvMediaPlayer`: playlist (list of URLs), done_callback, duck/unduck | `stop()` **invokes** the pending done_callback. |
| `player/libmpv.py` | `LibMpvPlayer`: real mpv wrapper, effective volume = user × duck factor | Only an `end-file` with reason 0 (EOF) fires the callback. `stop(for_replacement=True)` drops the callback. Both mpv players have a name (`music`, `tts`) that is part of the logger name; `Volume (<reason>): set X x duck Y = Z` shows the effective volume. |
| `webrtc.py` | `WebRTCProcessor`: 10 ms (320 byte) framing for `webrtc_noise_gain` | Can return `b""` while buffering. The audio loop then skips the chunk. |
| `zeroconf.py` | mDNS registration, `update_address()` announces another IP without restarting mDNS | `start_discovery()` returns None when mDNS is disabled. |
| `network.py` | Connection health and address of the ESPHome server. `apply_keepalive()` (TCP keepalive on every accepted socket, called from `APIServer.connection_made`, fixed timings, ends half-open connections after about 10 s if the kernel accepts all options; each option is set on its own and `KeepaliveResult` says which ones failed, with a warning in the log). `changed_address()` (pure) and `follow_interface_address()` loop. `EsphomeEndpoint`: owns the listening server, `apply(ip)` binds the new listener before closing the old one and updates mDNS | The loop runs on `state.client_disconnected` (set in `connection_lost` for **every** client) and, as a fallback, every 30 s (`ADDRESS_CHECK_INTERVAL_SECONDS`). Retries with backoff (1 s up to 60 s) while the interface has no address or applying fails. Not started with `--host`. `EsphomeEndpoint.apply()` is idempotent: a retry after a failed mDNS update does not bind twice. |
| `sendspin_player.py` | `SendspinPlayer`: client for the control socket of a Sendspin player (`sendspin-cli`, socket `control.sock` in `--sendspin-control-dir`): `status-kv` polling thread (1 s, slower while unreachable), play/pause/stop/vol/mute, duck/unduck with owners. `parse_status()` (pure) | One line per connection, reply `ok`/`error`. `vol`/`mute`/`stop` address the whole **group**. Duck modes (every mode acts only while the player plays): `pulse` (default, delegates to `PulseStreamDucker`, no fallback to another mode), `stop`, `duck` (group volume, persisted as `sendspin_ducked_from/to`). Status mirrored by `MediaPlayerEntity.on_sendspin_status()`; LVA's own music has priority. Group volume/mute are not taken over while `is_ducking` (any mode: in `pulse` the Local Audio add-on passes the lowered stream on to its group). |
| `pulse_stream_ducker.py` | `PulseStreamDucker(application_name)`: lowers the PulseAudio/PipeWire sink inputs of one application (`pulsectl-asyncio`, asyncio task on the main loop, `start()`). Used for `sendspin-cli`. `is_application_stream()`, `plan_stream_volumes()` (pure decision) | `duck()`/`unduck()` are thread-safe (`call_soon_threadsafe`). Reacts to `sink_input` `new` events. Pending restore persisted (`sendspin_stream_ducked_from/to`) because stream-restore keeps the ducked volume. Reconnects with backoff 1–60 s. Logs an error when ducking is requested and no stream is found. |
| `sendspin_bridge.py` | `create_sendspin_player()` (before `ServerState`), `start_sendspin_bridge()` (on the loop: persistence callbacks, polling, stream ducker task) | Keeps the Sendspin start-up out of `__main__.py`. |
| `util.py` | `get_version()` (reads `version.txt`), `get_esphome_version()`, `call_all()`, interface/IP detection | |

Two mpv players share the volume: `state.tts_player` (announcements, chimes, TTS, timer) and `state.music_player` (media, can use `--music-output-device`). The pipeline ducks only `music_player`.

## Voice pipeline state (satellite.py)

Flags on the satellite instance: `_is_streaming_audio`, `_pipeline_active`, `_tts_url`, `_tts_played`, `_continue_conversation`, `_processing`, and for wake word verification `_voice_subscription_flags` (per connection), `_verdict_timer` and `_verification_ended`. Whether a timer rings is `state.timer_alarm.ringing` (`satellite.timer_ringing` reads it).

Flow: `wakeup()` (audio thread) → blocked if `state.muted` or `_pipeline_active` → duck, play wake sound → on finish send `VoiceAssistantRequest(start=True)`, set `_is_streaming_audio=True`, emit `listening` → HA events in `handle_voice_event()`:
- `RUN_START`: store tts url, `_pipeline_active=True`
- `STT_VAD_END`/`STT_END`: stop streaming. `STT_END` emits `stt_text`.
- `INTENT_START`: emit `thinking`, optional thinking sound
- `INTENT_PROGRESS` with `tts_start_streaming=1`: `play_tts()` early
- `INTENT_END` with `continue_conversation=1`: remember it
- `TTS_END`: `play_tts()`, which is idempotent via `_tts_played`
- `RUN_END`: if no TTS was played, call `_tts_finished()` now. Otherwise `_pipeline_active` stays True until playback ends, so speaker audio can't trigger the wake word.

Wake word verification (`satellite.wake_word_audio_active`: HA subscribed with `VoiceAssistantSubscriptionFlag.WAKE_WORD_AUDIO`, switch *Wake word verification* on, not output only): `wakeup()` sends the start request with the audio before the detection at once, no wake sound, no duck, and starts `_verdict_timer` (2 s). `WAKE_WORD_END` stops it and ducks. `WAKE_WORD_REJECTED` stops streaming, emits `wake_word_rejected` (v2 only) and `idle`. The timeout sends `VoiceAssistantRequest(start=False)`, emits `pipeline_error` and `idle` 1 s later. After a rejection or timeout `_verification_ended` makes the following `RUN_END`/`ERROR` quiet; `RUN_START` resets it.

Ducking has owners (`duck(owner=...)`/`unduck(owner=...)` on the satellite and on `MpvMediaPlayer`; free text, but `MpvMediaPlayer` accepts only the known owners and raises `ValueError` otherwise): `conversation` (default), `timer` (ringing) and `announce` (voice announcements, handed over to `conversation` when `start_conversation` is set) and `media_announcement` (media player announcements, in `MediaPlayerEntity`). The volume is restored when the last owner has unducked. `ServerState.duck_music()`/`unduck_music()` is the one path that reaches both LVA's music player and a Sendspin player (`state.sendspin_player`); `MediaPlayerEntity` ducks the Sendspin player itself for `media_announcement`. With `--mpv-player-duck-mode pause` the music player (not the voice player) pauses instead of lowering the volume; `MpvMediaPlayer.is_paused` does not count a pause that only served ducking. A new reason to duck gets its own owner, and whoever ducks must unduck with the same owner.

`_tts_finished()` sends `VoiceAssistantAnnounceFinished`, removes the stop word from the active set, then either restarts listening after `continue_conversation_delay` (via `threading.Timer`) or unducks and emits `idle`.

**Stop word gating:** the stop word only triggers when `state.stop_word.id in state.active_wake_words`. It is added during TTS, announcements and timer ringing, and removed in `stop()` / `_tts_finished()` / timer stop. Forgetting the removal leaves "stop" live forever.

Timers: every HA timer event goes to `state.timer_alarm.handle_event()`, which applies it to `state.timers` (see `timers.py`). The countdown rings a timer when its end time has passed; `FINISHED` from HA makes it due at once. Each timer auto-stops `--timer-max-ring-seconds` after it started ringing. `CANCELLED` emits `timer_updated` with `seconds_left: 0`, then `idle` only if no timer is left. `connection_lost()` leaves a ringing timer alone (sound, `timer` ducking, stop word). Without a satellite, `process_audio()` keeps running while a timer rings, with the wake words paused, so the stop word works offline.

Wake word slots: HA can activate up to 2 wake words. `preferences.active_wake_words` is a 2-slot list (it may contain `None`), and words keep their slot when the set changes. Sensitivity entity 1/2 map to slot 0/1. The audio thread reloads its model list when `state.wake_words_changed` is True.

`connection_lost()` stops playback only when the **last** client disconnects. The music player keeps playing when LVA closed the connection itself to reload the wake word list (`_keep_music_on_disconnect`).

## Recurring patterns

**Satellite entity "get-or-create + reattach"** (satellite `__init__`): reuse the existing entity from `state`, re-append it if it's missing, then always reassign `entity.server = self` and refresh the callbacks (`update_get_*`/`update_set_*`), and call `sync_with_state()`. Follow the same shape for new built-in entities.

**Peripheral opt-in entities** (Light, Button, ButtonLock): a peripheral sends `register_*` → `PeripheralAPIServer._register_*` sets `state.pending_*` → `satellite.register_pending_*()` materialises it idempotently (also called from satellite `__init__` on reconnect) → `_schedule_ha_reconnect_for_late_entity()` closes the HA transport once (1.5 s debounce, 60 s cooldown) so HA re-enumerates entities.

**HA quirk:** `ButtonEventSensorEntity` must not yield an `EventResponse` with an empty `event_type`. HA then fails the whole config entry.

**Feature detection on aioesphomeapi:** `_HAS_AUDIO_DATA2` checks the protobuf descriptor. Do this for newer proto fields, because `aioesphomeapi` is pinned (`==46.30.1`).

## Change checklists

**New CLI option**
1. `__main__.py` argparse. If the app needs it at runtime, add a `ServerState` field with a default (otherwise `make_state()` in conftest has to handle it).
2. `docker-entrypoint.sh` env → flag mapping.
3. `.env.example`.
4. `docs/installation_part2_application.md`: systemd `Environment=` block.
5. `docs/configuration.md` (section "Parameter overview"): one row with parameter, env var, description and default.

Existing env quirk: `REFACTORY_SECONDS` is misspelled on purpose (it's the public name). Don't "fix" it.

**New peripheral event or command**
1. Add a member to the `LVAEvent`/`LVACommand` enum and a branch in `_dispatch_command`.
2. Update the module docstring in the module of that version (`peripheral_api/v1/api.py`, `v2/api.py`) and its spec page (`docs/peripheral_api/vN.md`). Version 1 is frozen: only bug fixes (`timer_auto_stopped` is one), new features go into a newer version.
3. If it represents an ongoing visual state, add it to `_STATE_EVENTS` in `emit_event()` (it's replayed on connect).
4. Maybe update `examples/*` peripherals and `examples/Peripheral web console/`, since #423 touched all of them.
5. Tests in `tests/unit/test_peripheral_api_v1.py` (`dispatch()` helper). Server and routing: `test_peripheral_api.py`, `test_peripheral_api_versions.py`.

**New HA-visible entity**
1. Class in `entity.py` (+ `__all__`).
2. Optional slot on `ServerState`.
3. Get-or-create block in satellite `__init__`.
4. Persist through `Preferences` if needed.
5. Document it in `docs/configuration.md`.

**New preference**: add the field with a default to `Preferences`, read it in `main()` before `ServerState` is built, and write it through a `persist_*`/setter that calls `state.preferences.save(state.preferences_path)`.

## Code structure for new logic

`process_audio()` in `__main__.py` and `satellite.py` are already large. Do not grow them.

- Put new logic that can run on its own (detection, gating, filtering, state decisions) into its own module under `linux_voice_assistant/`, as a plain function that takes the state it needs as parameters and returns the result. Keep mutable state in the caller (for example a flag passed in and returned) instead of module globals.
- Leave only a single call in `__main__.py` / `satellite.py`. Do not hide threading, I/O or hardware access in the new module, so it stays unit-testable with `make_state()` and mocks.
- Add `tests/unit/test_<module>.py` for every new module (see Tests below).
- Log decisions (detected, gated, reset) at debug level with the value and the threshold, in the style of the existing wake word log lines.
- Do not refactor unrelated code in the same change. Extract only the logic you are touching.

## Tests

- `tests/unit/conftest.py`: `make_state(tmp_path, **overrides)` auto-fills required `ServerState` fields. `make_satellite(tmp_path, state_overrides)` patches the sensitivity entities and sets `satellite._writelines = MagicMock()` so sent frames can be inspected.
- The players, stop word and peripheral API are MagicMocks. Assert on calls like `state.tts_player.play.assert_called_with(...)`.
- `asyncio_mode = "auto"`, so `async def test_*` works without a decorator.
- One test file per module (`test_unit.py` covers `util.py`).
- Pylint for tests uses `tests/.pylintrc`.

## Gotchas

- `ServerState.audio_input_channels` defaults to 1 and argparse (`--audio-input-channels`) also defaults to **1**, so the effective default is mono. The option only controls capture (2 = mic + AEC reference channel). What is streamed (`data` = channel 0 after WebRTC, `data2` = channel 0 before WebRTC) does not depend on it. Audio flow: see `docs/development/audio_pipeline.md`.
- In Docker the working directory is `/app`, so the default `--wake-word-root-dir` is `/app/wakewords`. Below it, `internal` ships in the image, `downloaded` and `custom` are named volumes (`wakeword_downloaded`, `wakeword_custom`) and each has a `microwakeword` and an `openwakeword` subdirectory. `__main__.py` builds the search list lowest priority first (internal, downloaded, custom) and creates all directories. HA wake words (always microWakeWord) are stored in `downloaded/microwakeword`. The removed `--wake-word-dir` / `--download-dir` (and `WAKE_WORD_DIR`, which the Docker entrypoint still passes on) are still accepted and ignored with a warning (`warn_about_removed_options()` in `util.py`). Bare metal uses `wakewords/` inside the repo.
- Paths in `.env.example` and the docs are written for the container (`/app/...`). The systemd examples use `/home/pi/linux-voice-assistant/...`.
- `mypy` runs non-strict with `python_version = 3.11` (`mypy.ini`). Don't use syntax newer than 3.11.
- The Docker image is **not** built locally by compose. `docker-compose.yml` pulls `ghcr.io/ohf-voice/linux-voice-assistant:${LVA_IMAGE_TAG:-latest}` (dev file: `-pr` image, tag `nightly`). The `Dockerfile` installs the pinned `aioesphomeapi` through `./script/setup`, so a fresh build always matches `pyproject.toml`. If a user reports an `AttributeError` on an aioesphomeapi symbol that exists in `.venv`, first suspect a stale or wrong image tag (check `LVA_IMAGE_TAG` in `.env`) before changing code. `satellite.py` guards `MULTI_CHANNEL_AUDIO` with `getattr` as a safety net only.
- The Compose files set no `container_name`, so every checkout or worktree is its own Compose project (named after its directory) with its own containers (`<project>-linux-voice-assistant-1`) and volumes. Use `docker compose logs linux-voice-assistant` instead of `docker logs`. LVA runs with `network_mode: host`, so to run two at the same time give each its own `PORT`, `PERIPHERAL_PORT` and `CLIENT_NAME` in its `.env`.
- Some older docstrings in modules not touched recently may still be Google style. New code must use the Sphinx `:param:` style from AGENTS.md.

## Defaults of CLI options

Set the default of a CLI option as a literal in its `parser.add_argument(default=...)` in `__main__.py`, not in a module constant that `__main__.py` imports. Everyone who looks for the default finds it next to the option, its help text and `%(default)s`.

## Documentation map

| Doc | Contents |
|-----|----------|
| `README.md` | Features, hardware, install entry points, dev setup |
| `docs/configuration.md` | Parameter overview at the top (CLI option, env var, description, default), then every HA device-page entity, from the user's view |
| `docs/installation.md` → `installation_part1_audioserver.md`, `installation_part2_application.md` | Install flow (PipeWire/PulseAudio, Docker Compose, systemd) |
| `docs/features.md` | Feature guides: AEC, listen during wake sound, API encryption, own sound files, wake words (models, directories, custom), stop word |
| `docs/behavior.md` | User-visible behavior (phases, ducking, stop word, timers, announcements). Binding for code changes, see AGENTS.md |
| `docs/peripheral_api/readme.md` | Versions of the WebSocket API and how to choose one. The protocol of each version and what LVA does for a peripheral is in `docs/peripheral_api/vN.md`. Binding for code changes, see AGENTS.md |
| `docs/development/communication.md` | Overview of the ESPHome API, mDNS and peripheral WebSocket interfaces, the voice request sequence, startup order, threads |
| `tests/e2e/README.md` | End-to-end test setup: virtual mic/speaker, fake Home Assistant, marker tones, CI job |
| `docs/development/audio_pipeline.md` | Mic input to HA streaming: capture, Mic Volume scalar, WebRTC, wake/stop word, `VoiceAssistantAudio` data/data2 |
| `docs/features.md` | Echo cancellation via PulseAudio/PipeWire |
| `docs/docker/README.md`, `docs/docker/local-audio/` | LVA together with the Music Assistant Local Audio (Sendspin) player: Compose project of the player (a copy of the add-on's, kept in step by hand), sound server, control socket, user |
| `docs/debugging.md` | Audio stack / container socket troubleshooting |
| `examples/*/` | Peripheral containers (Satellite1, ReSpeaker 2/4-mic, USB array, Jabra, web console) using the WebSocket API |
| `.github/workflow.md` | Docker build workflows (release/nightly/PR) |
| `.kilo/skill/review-pr/` | PR review procedure + `REVIEW_STANDARDS.md` (severity tags, no conventional-commit prefixes in PR titles) |

## Contribution constraints

- **Never commit or push directly to `main`**, whatever the instruction says (even "commit and push to main"). Every change, documentation only included, goes on its own branch and into a PR against `main`. If asked to push to `main`, do not do it: say so and offer a branch and a PR instead.
- PR titles describe the change functionally, with **no** `feat:`/`fix:` prefixes. Labels come from the ticked box in `.github/PULL_REQUEST_TEMPLATE.md`.
- `.github/AI_POLICY.md`: no autonomous contributions, a human must review everything, and nothing is posted to GitHub without explicit consent.
- The PR template checkbox "The code change is tested and works locally." may only be ticked by a human, by hand. AI agents never tick it, whatever tests they ran, and leave it unchecked in every PR description they write.
- `CHANGELOG.md` is generated from release notes. Don't edit it by hand.
- `breaking_change.md` is written by workflows (`.github/scripts/breaking_change.py`) from the "Breaking change migration" section of `breaking-change` PRs. Don't edit it by hand. Details: `.github/workflow.md`.
- AGENTS.md mentions a `stable` branch for backports, but `origin` currently has none. Check `git branch -r` before suggesting `backport-to-stable`.
