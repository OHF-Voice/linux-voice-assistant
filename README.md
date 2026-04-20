# Linux-Voice-Assistant

[![CI](https://github.com/OHF-Voice/linux-voice-assistant/actions/workflows/docker-build-release.yml/badge.svg)](https://github.com/OHF-Voice/linux-voice-assistant/actions/workflows/docker-build-release.yml) [![GitHub Package Version](https://img.shields.io/github/v/tag/OHF-Voice/linux-voice-assistant?label=version)](https://github.com/OHF-Voice/linux-voice-assistant/pkgs/container/linux-voice-assistant) [![GitHub License](https://img.shields.io/github/license/OHF-Voice/linux-voice-assistant)](https://github.com/OHF-Voice/linux-voice-assistant/blob/main/LICENSE.md) [![GitHub last commit](https://img.shields.io/github/last-commit/OHF-Voice/linux-voice-assistant)](https://github.com/OHF-Voice/linux-voice-assistant/commits) [![GitHub Container Registry](https://img.shields.io/badge/Container%20Registry-GHCR-blue)](https://github.com/OHF-Voice/linux-voice-assistant/pkgs/container/linux-voice-assistant)

An experimental Linux-Voice-Assistant software for [Home Assistant](https://www.home-assistant.io/) remote voice control and interaction.

This project enables you to build a Linux-based voice assistant designed to use [Assist](https://www.home-assistant.io/voice_control/) for Home Assistant. It allows you to create your own smart speaker that runs on any x64 or ARM64 hardware capable of handling local audio processing (using PulseAudio).

Unlike simpler voice satellites that run on microcontrollers with very limited compute power, this setup can perform local wake word detection (OWW/MWW) and process some data on-device. 

Because it runs on a full Linux system and offers access significantly more local computing resources for additional features and other integrations on the same satellite, this approach also provides greater flexibility for customization (such as for example experiment with using PipeWire).

[![A project from the Open Home Foundation](https://www.openhomefoundation.org/badges/ohf-project.png)](https://www.openhomefoundation.org/)

## Features

- Works with [Home Assistant](https://www.home-assistant.io/integrations/esphome/) using the [ESPHome](https://esphome.io/) protocol/API (via [aioesphomeapi](https://github.com/esphome/aioesphomeapi))
- Feature local on-device wake word detection using integrated [OpenWakeWord](https://github.com/dscripka/openWakeWord) or [MicroWakeWord](https://github.com/kahrendt/microWakeWord)
- Supports multiple wake words and languages
- Supports multiple architectures (linux/amd64 and linux/aarch64)
- Automated builds with artifact attestation for security
- Supports announcments, start/continue conversation, and timers
- Tested with Python 3.13 and Python 3.11.
- Prebuild docker image available on [GitHub Container Registry](https://github.com/OHF-Voice/linux-voice-assistant/pkgs/container/linux-voice-assistant)
- Prebuild Raspberry Pi image

## Usage

### Hardware

A more extensive list for possible compatible hardware can be found in the [PiCompose documentation](https://github.com/florian-asche/PiCompose) but basically any microphone that works with [PipeWire (multimedia framework for Linux)](https://pipewire.org/) can in theory be used for voice input with the prebuild image from there, you should however preferably use a far-field microphone-array solution if want better result. 

Two solutions recommended for test setups today is to use a Raspberry Pi Zero 2 W SBC (Single Board Computer with built-in WiFi) in combination with the [Satellite1 Hat Board](https://futureproofhomes.net/products/satellite1-top-microphone-board) or the [Respeaker Lite](https://wiki.seeedstudio.com/reSpeaker_usb_v3/). Those have microphone-array designed for far-field voice capture with the added benefit of using an onboard XMOS DSP microcontroller with custom firmware which does advanced audio pre-processing for microphone cleanup that result in very good voice recognition capabilities (as it runs algorithms for Noise Suppression, Acoustic Echo Cancellation, Interference Cancellation, and Automatic Gain Control). 

Alternativly if on a lower budget then suggest could try other untested microphone-array boards like example the [reSpeaker 2-Mics Pi HAT V2.0](https://wiki.seeedstudio.com/ReSpeaker_2_Mics_Pi_HAT/) (which uses a much more basic audio codec chip).

As for the minimum required compute performance on these satellites the target reference hardware for testing is currently a 64-bit ARM-based SBC based on Raspberry Pi RP3A0 SiP (System-in-Package); which means the Raspberry Pi Zero 2 W, Raspberry Pi Compute Module 3E (Raspberry Pi CM3E), or other development boards that uses the Compute Module Zero" (Raspberry Pi CM0), as all of which have similar specifications to the Raspberry Pi 3 B/B+ but with a CPU running at a lower frequency.

But you can also install LVA on AMD64 devices, for example on your Linux desktop computer.

### Software

#### Installation

For Raspberry Pi users, we provide a prebuild image that can be flashed to a SD card. See [PiCompose](https://github.com/florian-asche/PiCompose).

For all other users, we have different installation methods available (Docker, systemd), each with its own dedicated instructions. See [Linux-Voice-Assistant - Installation](docs/install.md). 

#### Parameter overview

💡 **Note:** There is an [environment variable](docs/install_application.md#environment-variables-reference) for each parameter if you use docker or systemd based setup.

``` sh
usage: __main__.py [-h] [--name NAME] [--audio-input-device AUDIO_INPUT_DEVICE] [--list-input-devices] [--audio-input-block-size AUDIO_INPUT_BLOCK_SIZE] [--audio-output-device AUDIO_OUTPUT_DEVICE] [--list-output-devices] [--wake-word-dir WAKE_WORD_DIR]  [--mic-auto-gain] [--mic-noise-suppression]
                   [--wake-model WAKE_MODEL] [--stop-model STOP_MODEL] [--download-dir DOWNLOAD_DIR] [--refractory-seconds REFRACTORY_SECONDS] [--wakeup-sound WAKEUP_SOUND] [--timer-finished-sound TIMER_FINISHED_SOUND] [--processing-sound PROCESSING_SOUND]
                   [--mute-sound MUTE_SOUND] [--unmute-sound UNMUTE_SOUND] [--preferences-file PREFERENCES_FILE] [--host HOST] [--network-interface NETWORK_INTERFACE] [--port PORT] [--enable-thinking-sound] [--debug]
```

| Parameter | Description | Default |
|-----------|-------------|---------|
| `--name` | Name of the voice assistant device (required) | Autogenerated (`lva-MAC-ADDRESS`) |
| `--audio-input-device` | Soundcard name for input device | Autodetected |
| `--audio-input-block-size` | Audio input block size in samples | 1024 |
| `--audio-output-device` | mpv name for output device | Autodetected |
| `--mic-volume` | Control microphone volume | 1.0 |
| `--mic-auto-gain` | Add WebRTC Gain to Mic | 0 |
| `--mic-noise-suppression` | Add WebRTC Noise Suppression to Mic | 0 |
| `--wake-word-dir` | Directory with wake word models (.tflite) and configs (.json) | `wakewords/` |
| `--wake-model` | ID of active wake word model | `okay_nabu` |
| `--stop-model` | ID of stop model | `stop` |
| `--download-dir` | Directory to download custom wake word models, etc. | `local/` |
| `--refractory-seconds` | Seconds before wake word can be activated again | 2.0 |
| `--timer-max-ring-seconds` | Seconds after which the timer stops ringing | 900.0 |
| `--wakeup-sound` | Sound file played when wake word is detected | `sounds/wake_word_triggered.flac` |
| `--timer-finished-sound` | Sound file played when timer finishes | `sounds/timer_finished.flac` |
| `--processing-sound` | Sound played while assistant is processing | `sounds/processing.wav` |
| `--mute-sound` | Sound played when muting the assistant | `sounds/mute_switch_on.flac` |
| `--unmute-sound` | Sound played when unmuting the assistant | `sounds/mute_switch_off.flac` |
| `--preferences-file` | Path to preferences JSON file | `preferences.json` |
| `--host` | IP-Address for ESPHome server, use 0.0.0.0 for all | Autodetected |
| `--network-interface` | Network interface for ESPHome server | Autodetected |
| `--port` | Port for ESPHome server | 6053 |
| `--enable-thinking-sound` | Enable thinking sound on startup | False |
| `--debug` | Print DEBUG messages to console | False |
| `--output-only` | Enable output only mode | False |

💡 **Note:** There is a detailed explanation on the gain, noise suppression, and wake word sensitivity flags in the [audio options](docs/audio_options.md) file.


## Build Information

Image builds can be tracked in this repository's `Actions` tab, and utilize [artifact attestation](https://docs.github.com/en/actions/security-guides/using-artifact-attestations-to-establish-provenance-for-builds) to certify provenance.

The Docker images are built using GitHub Actions, which provides:

- Automated builds for different architectures
- Artifact attestation for build provenance verification
- Regular updates and maintenance

The documentation for the build process can be found in the [GitHub Actions Workflows](.github/workflow.md) file.

## Development

### Code Quality Checks

The project uses the following tools to ensure code quality:
- **Black**: Code formatting (88 characters per line, PEP 8 compliant)
- **isort**: Import sorting compatible with Black
- **flake8**: Style and syntax checks
- **pylint**: Code quality checks
- **mypy**: Static type analysis

### Setup

To use the development tools (linting, testing, etc.), you need to install the required dependencies:

``` sh
./script/setup --dev
source .venv/bin/activate
```

### Linting Commands

#### Run all linting checks
``` sh
./script/lint...
```

#### Individual linting commands (with auto-fix support)

| Script | Description | Auto-fix Available? |
|--------|-------------|---------------------|
| `./script/lint_black` | Checks Python code formatting with Black | Yes, use `--auto` flag |
| `./script/lint_flake8` | Runs style and syntax checks with flake8 | No |
| `./script/lint_isort` | Checks import sorting with isort | Yes, use `--auto` flag |
| `./script/lint_mypy` | Runs static type analysis with mypy | No |
| `./script/lint_pylint` | Runs code quality checks with pylint | Yes, use `--auto` flag |

#### Examples

Run a specific lint check:
``` sh
./script/lint_black
```

Auto-fix formatting issues (Black + isort):
``` sh
./script/lint_black --auto
./script/lint_isort --auto
```

### Testing

Run the test suite:
``` sh
./script/test
```

## License

This project is licensed under the Apache 2.0 License - see the [LICENSE](LICENSE) file for details.
