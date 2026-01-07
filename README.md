# Linux Voice Assistant

An alpha Linux multi-instance voice satellite for [Home Assistant][homeassistant] using the [ESPHome][esphome] protocol. Turn any Linux device into a voice-controlled assistant with wake word detection, voice conversations, announcements, and timer support.

**Created by:** [Michael Hansen](https://github.com/synesthesiam) and [The Home Assistant Authors](https://github.com/OHF-Voice)

## Features

- 🎙️ **Local Wake Word Detection** - Multiple wake words (Alexa, Hey Jarvis, Okay Nabu, and more) using microWakeWord and openWakeWord
- 💬 **Full Voice Pipeline** - Speech-to-text, intent processing, and text-to-speech through Home Assistant
- 📢 **Announcements** - Play TTS announcements with automatic music ducking
- ⏲️ **Timer Support** - Voice-controlled timers with callbacks
- 🔄 **Multi-Instance** - Run multiple voice satellites on the same machine with unique wake words and audio devices
- 🚀 **Production Ready** - Systemd service integration with automatic restarts and persistent sessions
- 🎛️ **Audio Flexibility** - Supports PulseAudio echo cancellation and custom audio devices

Runs on Linux `aarch64` and `x86_64` platforms. Tested with Python 3.13 and Python 3.11.

## Installation

### System Requirements

Install required system dependencies:

```sh
sudo apt-get update && sudo apt-get install -y portaudio19-dev build-essential libmpv-dev
```

Individual packages:
* `libportaudio2` or `portaudio19-dev` - Audio input support (soundcard library)
* `build-essential` - Compilation tools for pymicro-features
* `libmpv-dev` - Audio output and media playback

### Quick Start

Clone the repository and run setup:

```sh
git clone https://github.com/OHF-Voice/linux-voice-assistant.git
cd linux-voice-assistant
script/setup
```

This creates a Python virtual environment and installs all dependencies.

## Usage

### Development Mode (Foreground)

For testing and development, run an instance in the foreground:

```sh
script/run --name "MyVoiceAssistant"
```

This auto-creates preference files in `preferences/user/` and assigns a unique port and MAC address.

### Production Deployment (Background Service)

For production use on servers or Raspberry Pi, deploy as a systemd service:

```sh
script/deploy MyVoiceAssistant
```

This will:
- Create all necessary preference files
- Auto-assign a unique port (starting from 6053) and MAC address
- Install a systemd user service
- Enable persistent user sessions (survives reboots and SSH disconnects)
- Start the service immediately

#### Deploy Multiple Instances

You can run multiple voice satellites on the same machine with different wake words and audio devices:

```sh
script/deploy Kitchen LivingRoom Bedroom --wake-model hey_jarvis
```

Each instance gets its own port, MAC address, and configuration.

### Managing Instances

```sh
script/status                  # Show all instances and service status
script/restart                 # Restart all running instances
script/stop MyVoiceAssistant   # Stop a specific instance
script/remove Kitchen          # Remove instance and service
```

### Audio Device Configuration

List available devices:

```sh
script/run --name Test --list-input-devices   # List microphones
script/run --name Test --list-output-devices  # List speakers
```

Configure devices during deployment:

```sh
script/deploy MyVA --audio-input-device 1 --audio-output-device "hdmi"
```

**Important:** Microphone must support 16kHz mono audio.

## Wake Word Configuration

### Default Wake Words

The following wake words are included:
- `okay_nabu` (default)
- `alexa`
- `hey_jarvis`
- `hey_mycroft`
- `hey_luna`
- `okay_computer`

Change the wake word:

```sh
script/deploy MyVA --wake-model hey_jarvis
```

### Custom Wake Words

Add custom wake words from the [Home Assistant Wake Words Collection][wakewords-collection]:

1. Download a `.tflite` model (e.g., `glados.tflite`)
2. Create a config file `glados.json`:

```json
{
  "type": "openWakeWord",
  "wake_word": "GLaDOS",
  "model": "glados.tflite"
}
```

3. Place both files in a directory and add it:

```sh
script/run --name MyVA --wake-word-dir /path/to/custom/wakewords
```

The system supports both [microWakeWord][microWakeWord] and [openWakeWord][openWakeWord] models.

## Connecting to Home Assistant

1. In Home Assistant, go to **Settings → Devices & services**
2. Click **Add Integration**
3. Search for and select **ESPHome**
4. Choose **Set up another instance of ESPHome**
5. Enter your Linux device's IP address with port (default: `6053`)
   - Example: `192.168.1.100:6053`
6. Click **Submit**

Your voice satellite will appear as a new device with media player and sensor entities.

### Multi-Instance Setup

Each instance uses a unique port. Find assigned ports:

```sh
cat preferences/user/*_cli.json | grep port
```

Add each instance separately in Home Assistant using its unique port.

## Advanced Configuration

### Acoustic Echo Cancellation

Enable PulseAudio echo cancellation for better wake word detection:

```sh
pactl load-module module-echo-cancel \
  aec_method=webrtc \
  aec_args="analog_gain_control=0 digital_gain_control=1 noise_suppression=1"
```

Verify the devices are available:

```sh
pactl list short sources
pactl list short sinks
```

Use the echo-cancelled devices:

```sh
script/deploy MyVA \
  --audio-input-device 'Echo-Cancel Source' \
  --audio-output-device 'pipewire/echo-cancel-sink'
```

### Configuration Files

The system uses a two-tier preferences structure:

- **Per-instance CLI config**: `preferences/user/{NAME}_cli.json`
  - CLI arguments, port, MAC address, system info
- **Per-instance preferences**: `preferences/user/{NAME}.json`
  - Active wake words list
- **Global settings**: `preferences/user/ha_settings.json`
  - Home Assistant URL, token, wake word friendly names, history entity

Edit these files to customize behavior without changing command-line arguments.

### Template Defaults

Create custom defaults for new instances in `preferences/default/default_user_cli.json`:

```json
{
  "wake_model": "hey_jarvis",
  "audio_input_device": "1",
  "refractory_seconds": 3.0
}
```

New instances will inherit these settings unless overridden.

## Troubleshooting

### Port Already in Use

Each instance needs a unique port. The system auto-assigns ports starting from 6053. Set a custom port:

```sh
script/deploy MyVA --port 6055
```

Or change the base port for all instances:

```sh
export LVAS_BASE_PORT=7000
script/deploy MyVA
```

### Service Management

Check service logs:

```sh
journalctl --user -u MyVoiceAssistant.service -f
```

Restart a misbehaving instance:

```sh
systemctl --user restart MyVoiceAssistant.service
```

### Audio Issues

Verify your microphone supports 16kHz mono:

```sh
script/run --name Test --list-input-devices
```

Test audio capture in development mode to see real-time detection.

## Contributing

Contributions are welcome! This project uses:
- **black** + **isort** for code formatting
- **flake8**, **pylint**, **mypy** for linting
- **pytest** for testing

Development workflow:

```sh
script/setup --dev          # Install dev dependencies
script/format               # Format code
script/lint                 # Check code quality
script/test                 # Run tests
```

## License

Apache License 2.0 - See [LICENSE.md](LICENSE.md) for details.

## Credits

**Original Creator:** [Michael Hansen](https://github.com/synesthesiam) (synesthesiam)  
**Contributors:** [The Home Assistant Authors](https://github.com/OHF-Voice) and [community contributors](https://github.com/OHF-Voice/linux-voice-assistant/graphs/contributors)

Built with:
- [Home Assistant](https://www.home-assistant.io/) - Open source home automation
- [ESPHome](https://esphome.io/) - Device communication protocol
- [microWakeWord](https://github.com/kahrendt/microWakeWord) - Efficient wake word detection
- [openWakeWord](https://github.com/dscripka/openWakeWord) - Open source wake word models

<!-- Links -->
[homeassistant]: https://www.home-assistant.io/
[esphome]: https://esphome.io/
[microWakeWord]: https://github.com/kahrendt/microWakeWord
[openWakeWord]: https://github.com/dscripka/openWakeWord
[wakewords-collection]: https://github.com/fwartner/home-assistant-wakewords-collection
[glados]: https://github.com/fwartner/home-assistant-wakewords-collection/blob/main/en/glados/glados.tflite
