# Linux-Voice-Assistant (32-bit version)

[![CI](https://github.com/OHF-Voice/linux-voice-assistant/actions/workflows/docker-build-release.yml/badge.svg)](https://github.com/OHF-Voice/linux-voice-assistant/actions/workflows/docker-build-release.yml) [![GitHub Package Version](https://img.shields.io/github/v/tag/OHF-Voice/linux-voice-assistant?label=version)](https://github.com/OHF-Voice/linux-voice-assistant/pkgs/container/linux-voice-assistant) [![GitHub License](https://img.shields.io/github/license/OHF-Voice/linux-voice-assistant)](https://github.com/OHF-Voice/linux-voice-assistant/blob/main/LICENSE.md) [![GitHub last commit](https://img.shields.io/github/last-commit/OHF-Voice/linux-voice-assistant)](https://github.com/OHF-Voice/linux-voice-assistant/commits) [![GitHub Container Registry](https://img.shields.io/badge/Container%20Registry-GHCR-blue)](https://github.com/OHF-Voice/linux-voice-assistant/pkgs/container/linux-voice-assistant)

> **arm32 branch — 32-bit ARM (armhf) only**
>
> This branch targets **32-bit ARM** hardware: Raspberry Pi 2/3 running a 32-bit OS,
> Orange Pi / Allwinner H3/H5 boards, and any Armbian or Raspberry Pi OS device
> reporting `armv7l` or `armhf`.
>
> **MicroWakeWord only.** OpenWakeWord requires TensorFlow, which has no 32-bit ARM
> wheels. All wake word detection on this branch uses MicroWakeWord (TFLite).
>
> For x86-64 or 64-bit ARM hardware, use the
> [main branch](https://github.com/OHF-Voice/linux-voice-assistant).

An experimental voice satellite software for [Home Assistant](https://www.home-assistant.io/) remote voice control and interaction.

This project enables you to build a Linux-based voice assistant designed to use [Assist](https://www.home-assistant.io/voice_control/) for Home Assistant. It allows you to create your own smart speaker that runs on any x86 or ARM32 hardware capable of handling local audio processing (using PulseAudio).

Unlike simpler voice satellites that run on microcontrollers with very limited compute power, this setup can perform local wake word detection (MWW) and process some data on-device.

Because it runs on a full Linux system and offers access significantly more local computing resources for additional features and other integrations on the same satellite, this approach also provides greater flexibility for customization (such as for example experiment with using PipeWire).

[![A project from the Open Home Foundation](https://www.openhomefoundation.org/badges/ohf-project.png)](https://www.openhomefoundation.org/)

## Features

- Works with [Home Assistant](https://www.home-assistant.io/integrations/esphome/) using the [ESPHome](https://esphome.io/) protocol/API (via [aioesphomeapi](https://github.com/esphome/aioesphomeapi))
- Local on-device wake word detection using [MicroWakeWord](https://github.com/kahrendt/microWakeWord) (TFLite-based, armhf wheels via piwheels)
- Supports multiple wake words and languages
- Targets **linux/arm/v7 (armhf)** — Raspberry Pi 2/3, Orange Pi, Allwinner H3/H5 boards
- Supports [announcements](https://www.home-assistant.io/actions/assist_satellite.announce/), [start](https://www.home-assistant.io/actions/assist_satellite.start_conversation/)/continue conversation, and timers
- Tested and works with Python 3.11
- Pre-built `linux/arm/v7` Docker image on ghcr.io (tag `armhf`), or build it yourself on-device from `Dockerfile`
- Supports [Websocket API](./docs/peripheral_api.md) for peripherals (e.g. buttons, LEDs, etc.) to integrate with the voice assistant

Planning for future features is discussed in [Discussion #495](https://github.com/OHF-Voice/linux-voice-assistant/discussions/495).
## Requirements

- **Microphone:** Device must support 16kHz mono audio
- **CPU:** ARMv7 or later, 1 GHz+
- **Memory:** min. 512 MB
- **Storage:** The OS and software is around 4 GB
- **OS:** 32-bit ARM Linux (linux/arm/v7 armhf) — Raspberry Pi OS Lite 32-bit, Armbian, Debian Bookworm armhf

A more extensive list for possible compatible hardware can be found in the [PiCompose documentation](https://github.com/florian-asche/PiCompose) but basically any microphone that works with [PipeWire (multimedia framework for Linux)](https://pipewire.org/) can in theory be used for voice input with the prebuild image from there, you should however preferably use a far-field microphone-array solution if want better result.

Those mic-boards have microphone-array designed for far-field voice capture with the added benefit of using an onboard XMOS DSP microcontroller with custom firmware which does advanced audio pre-processing for microphone cleanup that result in very good voice recognition capabilities (as it runs algorithms for Noise Suppression, Acoustic Echo Cancellation, Interference Cancellation, and Automatic Gain Control).

Alternatively if on a lower budget then suggest could use other microphone-array boards like for example the [reSpeaker 2-Mics Pi HAT V2.0](https://wiki.seeedstudio.com/ReSpeaker_2_Mics_Pi_HAT/) (which uses a much more basic audio codec chip).

## Usage

### Installation

**Docker (recommended)**

Build the armhf image locally on the device, then run with Docker Compose:

```sh
git clone -b arm32 https://github.com/OHF-Voice/linux-voice-assistant.git
cd linux-voice-assistant
docker build --platform linux/arm/v7 -f Dockerfile -t linux-voice-assistant:armhf .
cp .env.example .env
# edit .env, then:
LVA_IMAGE=linux-voice-assistant LVA_IMAGE_TAG=armhf \
  docker compose -f docker-compose.yml up -d
```

**Bare metal / systemd**

For a direct install without Docker, see the full instructions in [Install Application](docs/install_application.md). The key extra steps versus a 64-bit install are adding piwheels as an extra pip index and installing `libopenblas0`.

### Parameter overview

💡 **Note:** There is an [environment variable](docs/install_application.md#environment-variables-reference) for each parameter if you use docker or systemd based setup.

```sh
usage: __main__.py [-h] [--name NAME] [--audio-input-device AUDIO_INPUT_DEVICE] [--list-input-devices] [--audio-input-block-size AUDIO_INPUT_BLOCK_SIZE] [--audio-output-device AUDIO_OUTPUT_DEVICE] [--music-output-device MUSIC_OUTPUT_DEVICE] [--list-output-devices] [--wake-word-dir WAKE_WORD_DIR]  [--mic-auto-gain] [--mic-noise-suppression]
                   [--wake-model WAKE_MODEL] [--stop-model STOP_MODEL] [--download-dir DOWNLOAD_DIR] [--refractory-seconds REFRACTORY_SECONDS] [--wakeup-sound WAKEUP_SOUND] [--timer-finished-sound TIMER_FINISHED_SOUND] [--processing-sound PROCESSING_SOUND]
                   [--mute-sound MUTE_SOUND] [--unmute-sound UNMUTE_SOUND] [--preferences-file PREFERENCES_FILE] [--host HOST] [--network-interface NETWORK_INTERFACE] [--mac-address MAC_ADDRESS] [--port PORT] [--enable-thinking-sound] [--listen-during-wake-sound] [--debug]
```

| Parameter                       | Description                                                                            | Default                              |
| ------------------------------- | -------------------------------------------------------------------------------------- | ------------------------------------ |
| `--name`                        | Name of the voice assistant device (required)                                          | Autogenerated (`lva-MAC-ADDRESS`)    |
| `--audio-input-device`          | Soundcard name for input device                                                        | Autodetected                         |
| `--audio-input-block-size`      | Audio input block size in samples                                                      | 1024                                 |
| `--audio-output-device`         | mpv name for output device, use a `pulse/...` name from `--list-output-devices`        | `pulse`                              |
| `--music-output-device`         | mpv name for the music/media output device                                             | `--audio-output-device`              |
| `--mic-volume`                  | Control microphone volume                                                              | 100                                  |
| `--mic-auto-gain`               | Add WebRTC Gain to Mic                                                                 | 0                                    |
| `--mic-noise-suppression`       | Add WebRTC Noise Suppression to Mic                                                    | 0                                    |
| `--audio-input-channels`        | Number of microphone audio channels to stream                                          | 2                                    |
| `--wake-word-dir`               | Directory with wake word models (.tflite) and configs (.json)                          | `wakewords/`                         |
| `--wake-model`                  | ID of active wake word model                                                           | `okay_nabu`                          |
| `--stop-model`                  | ID of stop model                                                                       | `stop`                               |
| `--download-dir`                | Directory to download custom wake word models, etc.                                    | `local/`                             |
| `--refractory-seconds`          | Seconds before wake word can be activated again                                        | 2.0                                  |
| `--continue-conversation-delay` | Delay before mic opens for continued conversation                                      | 0.5                                  |
| `--timer-max-ring-seconds`      | Seconds after which the timer stops ringing                                            | 900.0                                |
| `--wakeup-sound`                | Sound file played when wake word is detected                                           | `sounds/wake_word_triggered.flac`    |
| `--start-listening-sound`       | Sound file played when button is pressed to start listening                            | `sounds/start_listening_button.flac` |
| `--timer-finished-sound`        | Sound file played when timer finishes                                                  | `sounds/timer_finished.flac`         |
| `--processing-sound`            | Sound played while assistant is processing                                             | `sounds/processing.wav`              |
| `--mute-sound`                  | Sound played when muting the assistant                                                 | `sounds/mute_switch_on.flac`         |
| `--unmute-sound`                | Sound played when unmuting the assistant                                               | `sounds/mute_switch_off.flac`        |
| `--button-double-press-sound`   | Sound played on button double press                                                    | `sounds/button_double_press.flac`    |
| `--button-triple-press-sound`   | Sound played on button triple press                                                    | `sounds/button_triple_press.flac`    |
| `--button-long-press-sound`     | Sound played on button long press                                                      | `sounds/button_long_press.flac`      |
| `--pipeline-ended-sound`        | Sound played when the voice pipeline ends without a response (e.g. no speech detected) | `sounds/pipeline_ended_low.flac`     |
| `--preferences-file`            | Path to preferences JSON file                                                          | `preferences.json`                   |
| `--host`                        | IP-Address for ESPHome server, use 0.0.0.0 for all                                     | Autodetected                         |
| `--network-interface`           | Network interface for ESPHome server                                                   | Autodetected                         |
| `--mac-address`                 | MAC address the device is known by in Home Assistant (e.g. `02:00:00:00:00:01`)        | The network interface's              |
| `--port`                        | Port for ESPHome server                                                                | 6053                                 |
| `--enable-thinking-sound`       | Enable thinking sound on startup                                                       | False                                |
| `--listen-during-wake-sound`    | Start listening while the wake sound is still playing                                  | False                                |
| `--peripheral-host`             | Bind address for the peripheral WebSocket API                                          | 0.0.0.0                              |
| `--peripheral-port`             | Port for the peripheral WebSocket API                                                  | 6055                                 |
| `--peripheral-volume-step`      | Volume change per button press, 0.0–1.0                                                | %(default)s                          |
| `--peripheral-startup-wait`     | Seconds to wait for peripherals to register entities                                   | 2.0                                  |
| `--disable-peripheral-api`      | Disable the peripheral WebSocket API entirely                                          | False                                |
| `--debug`                       | Print DEBUG messages to console                                                        | False                                |
| `--colored-debug`               | Print colored DEBUG messages to console                                                | False                                |
| `--output-only`                 | Enable output only mode                                                                | False                                |

💡 **Note:** There are detailed explanations on the controlled entities from device page in the [configuration](docs/configuration.md) file.

💡 **Note:** How the assistant behaves in everyday situations (music ducking during conversations, announcements, timers, stop word, mute, volume) is described in the [behavior guide](docs/behavior.md).

## Build Information

The armhf Docker image is built from `Dockerfile` by GitHub Actions (under QEMU emulation, `linux/arm/v7` only) and published to ghcr.io with artifact attestation. Tags: `armhf` (latest stable release), `<version>-armhf` (releases), `nightly-armhf` (every push to `arm32`). You can also build the image yourself on the target device.

The build uses `python:3.11-slim-bookworm` as the base image for both build and runtime stages, and configures piwheels as an extra pip index so that armhf-native wheels for numpy, soundcard, and pymicro-wakeword are fetched without source compilation.

See [GitHub Actions Workflows](.github/workflow.md) for the full list of workflows and image tags.

## Development

### System Requirements

**System packages (Linux):**
- libmpv-dev (`sudo apt install libmpv-dev`)
- PulseAudio/PipeWire (`sudo apt install pulseaudio pipewire`)
- ALSA utils (`sudo apt install alsa-utils`)

**Python:**
- Python 3.11 must be installed on your system (`python3 --version` should show 3.11)
- On Debian Bookworm / Raspberry Pi OS: `sudo apt install python3.11 python3.11-venv python3.11-dev`
- Also install `libopenblas0` for the armhf numpy wheel: `sudo apt install libopenblas0`

### VS Code Setup

VS Code development uses a local Python virtual environment (`.venv/`):

1. Open workspace in VS Code
2. Accept Workspace Trust (bottom-left status bar)
3. Install recommended extensions when prompted:
   - `ms-python.python` - Python language support
   - `ms-python.vscode-pylance` - Python language server
   - `kilo.kilocode` - Kilo AI assistant
4. **Terminal** → **New Terminal** (or `` Ctrl+` ``) - Opens integrated terminal (uses .venv when available)
5. Run `./script/setup --dev` to create `.venv` and install dev dependencies
6. VS Code automatically detects `.venv/bin/python` as the Python interpreter

**Available VS Code Tasks:**
- `Ctrl+Shift+B` - Run Setup or Linter
- `Ctrl+Shift+T` - Run Tests
- `Ctrl+Shift+P` → "Tasks: Run Task" - Show all tasks (Setup, Linter, Tests, Run App)

### Code Quality Checks

The project uses the following tools to ensure code quality:

- **Black**: Code formatting (200 characters per line)
- **isort**: Import sorting compatible with Black
- **flake8**: Style and syntax checks
- **pylint**: Code quality checks
- **mypy**: Static type analysis

Docstrings are mandatory in Sphinx style with `:param:` (and `:raises:` where relevant). Google-style (`Args:`) and bullet-style docstrings are not accepted:

```python
def my_function(param1: str, param2: int) -> str:
    """
    Brief one-line description of the function.
    :param param1: Description of what param1 is used for.
    :param param2: Description of what param2 is used for.
    """
```

### Setup

To use the development tools (linting, testing, etc.), you need to install the required dependencies:

```sh
./script/setup --dev
source .venv/bin/activate
```

### Linting Commands

#### Run all linting checks

```sh
./script/lint...
```

#### Individual linting commands (with auto-fix support)


| Script                 | Description                              | Auto-fix Available?   |
| ------------------------ | ------------------------------------------ | ----------------------- |
| `./script/lint_black`  | Checks Python code formatting with Black | Yes, use`--auto` flag |
| `./script/lint_flake8` | Runs style and syntax checks with flake8 | No                    |
| `./script/lint_isort`  | Checks import sorting with isort         | Yes, use`--auto` flag |
| `./script/lint_mypy`   | Runs static type analysis with mypy      | No                    |
| `./script/lint_pylint` | Runs code quality checks with pylint     | Yes, use`--auto` flag |

#### Examples

Run a specific lint check:

```sh
./script/lint_black
```

Auto-fix formatting issues (Black + isort):

```sh
./script/lint_black --auto
./script/lint_isort --auto
```

### Testing

Run the test suite:
``` sh
./script/tests
```

## License

This project is licensed under the Apache 2.0 License - see the [LICENSE](LICENSE) file for details.
