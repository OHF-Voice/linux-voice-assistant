# Linux-Voice-Assistant

[![CI](https://github.com/OHF-Voice/linux-voice-assistant/actions/workflows/docker-build-release.yml/badge.svg)](https://github.com/OHF-Voice/linux-voice-assistant/actions/workflows/docker-build-release.yml) [![GitHub Package Version](https://img.shields.io/github/v/tag/OHF-Voice/linux-voice-assistant?label=version)](https://github.com/OHF-Voice/linux-voice-assistant/pkgs/container/linux-voice-assistant) [![GitHub License](https://img.shields.io/github/license/OHF-Voice/linux-voice-assistant)](https://github.com/OHF-Voice/linux-voice-assistant/blob/main/LICENSE.md) [![GitHub last commit](https://img.shields.io/github/last-commit/OHF-Voice/linux-voice-assistant)](https://github.com/OHF-Voice/linux-voice-assistant/commits) [![GitHub Container Registry](https://img.shields.io/badge/Container%20Registry-GHCR-blue)](https://github.com/OHF-Voice/linux-voice-assistant/pkgs/container/linux-voice-assistant)

An experimental voice satellite software for [Home Assistant](https://www.home-assistant.io/) remote voice control and interaction.

This project enables you to build a Linux-based voice assistant designed to use [Assist](https://www.home-assistant.io/voice_control/) for Home Assistant. It allows you to create your own smart speaker that runs on any x64 or ARM64 hardware capable of handling local audio processing (using PulseAudio).

Unlike simpler voice satellites that run on microcontrollers with very limited compute power, this setup can perform local wake word detection (OWW/MWW) and process some data on-device.

Because it runs on a full Linux system and offers access significantly more local computing resources for additional features and other integrations on the same satellite, this approach also provides greater flexibility for customization (such as for example experiment with using PipeWire).

News about this software: [Discussion #495](https://github.com/OHF-Voice/linux-voice-assistant/discussions/495).

![Linux Voice Assistant](docs/images/banner.svg)

[![A project from the Open Home Foundation](https://www.openhomefoundation.org/badges/ohf-project.png)](https://www.openhomefoundation.org/)

## Features

- **Home Assistant**
  - Connects via the [ESPHome](https://esphome.io/) protocol ([aioesphomeapi](https://github.com/esphome/aioesphomeapi)), discovered automatically via mDNS (can be turned off with `--enable-zeroconf 0`)
  - Optional [API encryption](./docs/features.md#feature-api-encryption), like ESPHome devices with an encryption key
  - Several Home Assistant clients at once, and several instances on one system (`--mac-address`)
  - [Announcements](https://www.home-assistant.io/actions/assist_satellite.announce/), [start](https://www.home-assistant.io/actions/assist_satellite.start_conversation/)/continue conversation and timers
  - Own media player entity, also for `announce: true`
- **Wake word and stop word**
  - Local wake word detection with [OpenWakeWord](https://github.com/dscripka/openWakeWord) or [MicroWakeWord](https://github.com/kahrendt/microWakeWord)
  - Two wake words or languages active at the same time (two slots, selectable in Home Assistant)
  - Stop word independent of Home Assistant, only active during TTS, announcements and a ringing timer
  - Sensitivity of both wake words and the stop word adjustable at runtime
- **Audio**
  - Separate players for music (own output device with `--music-output-device`) and voice
  - Music is ducked or paused while the assistant talks (`--mpv-player-duck-mode`)
  - Mic volume, gain and noise suppression (WebRTC), adjustable at runtime
- **Peripherals**
  - [WebSocket API](./docs/development/peripheral_api.md) for buttons, LEDs and other hardware (API version 1)
  - Single, double, triple and long press events are sent to Home Assistant, double, triple and long press with their own sounds
  - Button lock switch, LED light entity and remote mute (`--enable-remote-unmute`)
  - Software mute: with `--enable-remote-unmute 0`, only a peripheral (e.g. a physical mute switch) can unmute the microphone, Home Assistant cannot (see [Mute](./docs/configuration.md#mute-switch)). This is independent of a hardware mute of the microphone.
- **Runtime**
  - Settings and states are stored locally and restored on startup
  - Docker images for linux/amd64, linux/aarch64 and linux/arm/v7 on the [GitHub Container Registry](https://github.com/OHF-Voice/linux-voice-assistant/pkgs/container/linux-voice-assistant), built automatically with artifact attestation
  - Tested with Python 3.11 and 3.13

## Requirements

- **Microphone:** Device must support 16kHz mono audio
- **CPU:** 1Ghz
- **Memory:** min. 512MB
- **Storage:** The OS and software is around 4GB
- **OS:** linux/amd64, linux/aarch64 or 32-bit ARM linux/arm/v7

A more extensive list for possible compatible hardware can be found in the [PiCompose documentation](https://github.com/florian-asche/PiCompose) but basically any microphone that works with [PipeWire (multimedia framework for Linux)](https://pipewire.org/) can in theory be used for voice input with the prebuild image from there, you should however preferably use a far-field microphone-array solution if want better result.

Two solutions recommended for setups today is:

- use a Raspberry Pi Zero 2W (Single Board Computer with built-in WiFi) in combination with the [Satellite1 Hat Board](https://futureproofhomes.net/products/satellite1-top-microphone-board)
- use at least a Raspberry Pi 3 with the [Respeaker Lite](https://wiki.seeedstudio.com/reSpeaker_usb_v3/). The Respeaker Lite currently has a problem with the Zero 2W.

Those mic-boards have microphone-array designed for far-field voice capture with the added benefit of using an onboard XMOS DSP microcontroller with custom firmware which does advanced audio pre-processing for microphone cleanup that result in very good voice recognition capabilities (as it runs algorithms for Noise Suppression, Acoustic Echo Cancellation, Interference Cancellation, and Automatic Gain Control).

Alternatively if on a lower budget then suggest could use other microphone-array boards like for example the [reSpeaker 2-Mics Pi HAT V2.0](https://wiki.seeedstudio.com/ReSpeaker_2_Mics_Pi_HAT/) (which uses a much more basic audio codec chip).

## Usage

### Installation

Pick the setup that fits your system:

| | <img src="https://raw.githubusercontent.com/OHF-Voice/apps/main/assist_satellite/icon.png" alt="" height="28" align="absmiddle"> HomeAssistant OS | <img src="docs/images/logo.svg" alt="" height="28" align="absmiddle"> Raspberry Pi image | <img src="docs/images/logo.svg" alt="" height="28" align="absmiddle"> Docker / systemd |
|---|---|---|---|
| **How** | Install the [Assist Satellite](https://github.com/OHF-Voice/apps/tree/main/assist_satellite) app on your HomeAssistant. | Flash the [PiCompose](https://github.com/florian-asche/PiCompose) image, set Wi-Fi, power on. Everything is set up, and updated automatically. | Install on your own: [install the audio service](docs/installation.md#step-1-install-audioservice), then [install the application](docs/installation.md#step-2-install-application) with Docker Compose or on bare metal with systemd. |
| **Includes** | Linux-Voice-Assistant | Linux-Voice-Assistant, [Snapcast](https://github.com/florian-asche/docker-snapcast), [Music Assistant Local-Audio](https://github.com/music-assistant/local-audio-addon), [Bluetooth proxy](https://github.com/denvera/bt-proxy) | Linux Voice Assistant |
| **Start** | <a href="https://my.home-assistant.io/redirect/supervisor_add_addon_repository/?repository_url=https://github.com/OHF-Voice/apps"><img src="https://my.home-assistant.io/badges/supervisor_add_addon_repository.svg" alt="Add repository" height="34"></a><br><a href="https://my.home-assistant.io/redirect/supervisor_addon/?addon=assist_satellite&repository_url=https://github.com/OHF-Voice/apps"><img src="https://my.home-assistant.io/badges/supervisor_addon.svg" alt="Show app" height="34"></a> | [<img src="https://raw.githubusercontent.com/florian-asche/PiCompose/main/docs/picompose_plug_and_play.svg" alt="PiCompose" width="460">](https://github.com/florian-asche/PiCompose#installation) | [![Docker Compose](https://img.shields.io/badge/Docker%20Compose-2496ED?style=for-the-badge&logo=docker&logoColor=white)](docs/installation.md)<br>[![systemd](https://img.shields.io/badge/systemd-bare%20metal-30D475?style=for-the-badge&logo=linux&logoColor=white)](docs/installation.md) |

### Configuration and Parameters

All command line parameters, their defaults and the matching environment variables are listed in the [Parameter overview](docs/configuration.md#parameter-overview), and the controlled entities on the Home Assistant device page are explained in the [Dashboard](docs/configuration.md#dashboard--homeassistant-entities) and its [entities](docs/configuration.md#entities-on-the-device-page).

### Feature Guides

Setup guides for individual features are in [features](docs/features.md): [echo cancellation](docs/features.md#acoustic-echo-cancellation), [listening during the wake sound](docs/features.md#feature-listen-during-wake-sound), [API encryption](docs/features.md#feature-api-encryption), [own sound files](docs/features.md#use-own-soundfiles), [wake words](docs/features.md#wake-word) (including [custom wake words](docs/features.md#custom-wake-word)) and the [stop word](docs/features.md#stop-word).

### Debugging

If the audio does not work, see the [debugging guide](docs/debugging.md) for how the Linux audio stack and the container sockets fit together.

### Behavior

How the assistant behaves in everyday situations (music ducking during conversations, announcements, timers, stop word, mute, volume) is described in the [behavior guide](docs/behavior.md).

## Build Information

<details>
<summary><b>Show build information</b></summary>

Image builds can be tracked in this repository's `Actions` tab, and utilize [artifact attestation](https://docs.github.com/en/actions/security-guides/using-artifact-attestations-to-establish-provenance-for-builds) to certify provenance.

The Docker images are built using GitHub Actions, which provides:

- Automated builds for different architectures
- Artifact attestation for build provenance verification
- Regular updates and maintenance

The documentation for the build process can be found in the [GitHub Actions Workflows](.github/workflow.md) file.

</details>

## Development

<details>
<summary><b>System Requirements</b></summary>

**System packages (Linux):**
- libmpv-dev (`sudo apt install libmpv-dev`)
- PulseAudio/PipeWire (`sudo apt install pulseaudio pipewire`)
- ALSA utils (`sudo apt install alsa-utils`)

**Python:**
- Python 3.11+ must be installed on your system (`python3 --version` should show 3.11+)
- On Ubuntu/Debian: `sudo apt install python3.11 python3.11-venv python3.11-dev`

</details>

<details>
<summary><b>VS Code Setup</b></summary>

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

</details>

<details>
<summary><b>Code Quality Checks</b></summary>

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

</details>

<details>
<summary><b>Setup</b></summary>

To use the development tools (linting, testing, etc.), you need to install the required dependencies:

```sh
./script/setup --dev
source .venv/bin/activate
```

</details>

<details>
<summary><b>Linting Commands</b></summary>

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

</details>

<details>
<summary><b>Testing</b></summary>

Run the test suite:
``` sh
./script/tests
```

</details>

<details>
<summary><b>Developer documentation</b></summary>

How LVA communicates with Home Assistant and peripherals, and what happens during a voice request, is described in the [communication overview](docs/development/communication.md).

</details>

## License

This project is licensed under the Apache 2.0 License - see the [LICENSE](LICENSE.md) file for details.
