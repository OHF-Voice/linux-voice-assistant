# Default CLI Configuration

This folder contains the default template for voice assistant instance configuration.

## default_cli.json

This file serves as the template for all new voice assistant instances. When you create a new instance, the settings from this file are copied to `preferences/user/<name>_cli.json` and merged with any command-line arguments you provide.

### Configuration Options

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `name` | string | `""` | Name of this voice assistant instance (required) |
| `autostart` | boolean | `false` | Automatically start this instance on boot when using systemd service |
| `debug` | boolean | `false` | Print DEBUG messages to console for troubleshooting |
| `list_input_devices` | boolean | `false` | List all available audio input devices and exit |
| `list_output_devices` | boolean | `false` | List all available audio output devices and exit |
| `audio_input_device` | string | `""` | Soundcard name/ID for microphone input (see `--list-input-devices`) |
| `audio_input_block_size` | number | `1024` | Audio buffer size for microphone capture (samples per block) |
| `audio_output_device` | string | `""` | MPV device name for audio output (see `--list-output-devices`) |
| `wake_word_dir` | string | `""` | Custom directory containing wake word models (.tflite) and configs (.json) |
| `wake_model` | string | `"okay_nabu"` | ID of the active wake word model to use |
| `stop_model` | string | `"stop"` | ID of the stop word model |
| `download_dir` | string | `""` | Directory to download custom wake word models and other assets |
| `refractory_seconds` | number | `2.0` | Cooldown period (in seconds) before the wake word can be triggered again |
| `wakeup_sound` | string | `""` | Path to audio file played when wake word is detected |
| `timer_finished_sound` | string | `""` | Path to audio file played when a timer completes |
| `preferences_file` | string | `""` | Path to the main preferences JSON file (usually auto-set by run script) |
| `host` | string | `"0.0.0.0"` | IP address for the ESPHome-compatible server to bind to |
| `port` | number | `6053` | TCP port for the ESPHome-compatible server |
| `mac` | string | `""` | Spoof MAC address exposed to Home Assistant (format: `aa:bb:cc:dd:ee:ff`) |

## Usage

### Creating a New Instance

When you run:
```bash
script/run --name "my_assistant" --debug
```

The script will:
1. Copy `default_cli.json` to `preferences/default/` (if not already there)
2. Create `preferences/user/my_assistant_cli.json` with default values
3. Merge your CLI arguments (like `--debug`) into the file
4. Create `preferences/user/my_assistant.json` for runtime preferences

### Modifying Defaults

To change the default settings for all new instances, edit `default_cli.json` in this folder. Existing instances will not be affected.

### Per-Instance Configuration

Each instance stores its configuration in `preferences/user/<name>_cli.json`. You can edit these files directly to change settings for specific instances without affecting the defaults.

## Notes

- The `autostart` flag is used by systemd services to determine which instances to launch on boot
- Empty string values (`""`) mean "use the system default"
- Port numbers are auto-assigned when creating multiple instances to avoid conflicts
- MAC addresses are randomly generated if not specified, then persisted across restarts
