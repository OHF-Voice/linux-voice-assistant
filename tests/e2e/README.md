# End-to-end tests

These tests start a real LVA process and drive it the way a user and Home Assistant would, without hardware and without a real Home Assistant. Except for the tests in `peripheral_api/`, they do not use the peripheral WebSocket API.

```
 test ──paplay──▶ e2e_mic (null sink) ──▶ e2e_mic_source ──▶ LVA ──▶ e2e_speaker (null sink) ──parec──▶ test
                                                              │  ▲
                                                 ESPHome API  ▼  │
                                                    fake Home Assistant
```

| Part | File | Job |
|------|------|-----|
| Virtual audio | `audio.py` | Creates the null sinks, plays wav files into the microphone, records the speaker and detects tones and sound |
| LVA process | `lva.py` | Starts `python -m linux_voice_assistant --debug` with the virtual devices, collects the log. Tests wait for its debug log lines (`Detected wake word`, `Playing TTS response`, `TTS response finished`, `Stopping timer finished sound`, ...) to follow what LVA does |
| Fake Home Assistant | `fake_ha.py` | ESPHome API client (`aioesphomeapi`) that subscribes to the voice assistant, receives microphone audio, answers requests with the pipeline events and serves TTS/media URLs over HTTP |

One LVA instance runs for the whole session. Every test opens a fresh fake Home Assistant connection and waits for the wake word refractory period, so tests do not influence each other as long as each one ends with LVA idle.

## What the tests prove

Playback is verified without a microphone or speaker in two ways:

* **Into LVA:** `probe_tone.wav` (1 kHz) is played on the virtual microphone and must arrive as a 1 kHz tone in the audio LVA streams to Home Assistant. Speech files must trigger (or not trigger) the wake and stop word.
* **Out of LVA:** the fake Home Assistant hands out URLs of tone files (`tts_marker*.wav` 1.5 kHz, `music_first.wav` 0.8 kHz, `music_second.wav` 0.6 kHz). The recorder on the speaker monitor must hear exactly that tone, so the test knows that the right audio was played, how long, and that it stopped. The wake sound and the timer sound are checked for audible sound.

## Running locally

Needs `libmpv`, `pactl`, `paplay`, `parec` and a running PulseAudio or PipeWire-Pulse server. The tests create and remove their own virtual devices and skip when no server is available.

```bash
./script/e2e                                  # everything (LVA and peripheral API tests), about 4.5 minutes
./script/e2e --ignore=tests/e2e/peripheral_api  # only the LVA tests, as in CI
./script/e2e tests/e2e/peripheral_api         # only the peripheral API tests, as in CI
./script/e2e -k stop                          # selected tests
./script/e2e -s                               # also show the LVA log
```

Without a path argument `./script/e2e` runs all of `tests/e2e`. With a path argument only that path runs, other arguments go to pytest unchanged. Each selection works on its own: the `lva` instance only starts when a test asks for it.

The suite is not part of `./script/tests`, which only runs the fast unit tests. In CI the workflow `.github/workflows/e2e-tests.yml` (E2E-Tests) runs them in the same cases as the image builds (main, pull requests, version tags) as two jobs, each with its own job summary table (the docstring of every test is its description column) and annotations for failed tests: **LVA** (all tests except the peripheral API, artifact `e2e-results`) and **LVA-Peripheral-API-V1** (`tests/e2e/peripheral_api`, artifact `e2e-peripheral-results`). Each artifact holds the LVA log and `junit.xml` (set `E2E_LOG_DIR` to write the log to a directory locally, add `--junitxml=<file>` for the XML).

No real microphone, speaker or Home Assistant is involved. The tests show that LVA's logic and audio paths work, not that a specific sound card does.

## Peripheral API tests

`peripheral_api/` tests the peripheral WebSocket API the way a peripheral uses it, modelled on `examples/Peripheral web console`: the same registrations (light, button, button lock), the same commands and the events the console reacts to. They run against a second LVA instance (`peripheral_lva`), because registering entities adds them to the device and makes LVA reconnect Home Assistant once, which would change the entity list the other tests check. Run them alone with `./script/e2e tests/e2e/peripheral_api`, they have their own job `LVA-Peripheral-API-V1` in `e2e-tests.yml`.

## Fixtures

`fixtures/*.wav` are 16 kHz mono files: speech synthesized with Piper (`okay_nabu`, `hey_jarvis`, `stop`, `unrelated_speech`) and computed tones. They are committed, CI does not need Piper. `generate_fixtures.py` documents how to recreate them. Check a new speech file against the wake word model before committing it, a synthetic voice does not always trigger a model.
