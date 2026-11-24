# Breaking changes

What users have to change when updating. Entries are added automatically when a pull request labelled `breaking-change` is merged, newest first, and grouped under the release that shipped them.

## v1.1.16 (2026-10-24)

### #451 Dual audio channel enhancement

The default of `--audio-input-channels` (`AUDIO_INPUT_CHANNELS`) was changed from 1 to 2. It was reverted in #467 before any release contained it, so the default is 1 again. If you want two channels (microphone plus AEC reference), set `--audio-input-channels 2` / `AUDIO_INPUT_CHANNELS=2` explicitly.

## v1.1.7 (2026-03-11)

### #221 fix(config): remove unix: prefix from default pulseaudio socket path …

The default `LVA_PULSE_SERVER` in `.env.example` no longer has the `unix:` prefix (`/run/user/<uid>/pulse/native`). An existing `.env` keeps its value. When you copy the new default and your PulseAudio/PipeWire setup needs the prefix, set `LVA_PULSE_SERVER="unix:/run/user/<uid>/pulse/native"`.

## v1.1.4 (2026-02-20)

### #165 Feature/custom paths

The Docker Compose file mounts two additional named volumes, `wakeword_custom` (`/app/wakewords/custom`) and `sounds_custom` (`/app/sounds/custom`), and the entrypoint passes `WAKE_WORD_DIR` to the application. If you use your own compose file instead of the shipped one, add these volumes to use custom wake words and sounds. The PipeWire setup in the docs was changed as well; compare it with your audio server configuration.
