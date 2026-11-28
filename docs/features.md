# Acoustic Echo Cancellation

<details>
<summary><b>Show details</b></summary>

Acoustic Echo Cancellation (AEC) is a type of sound processing used to cancel out the noise coming out of your speaker and going into your mic. In LVA this functionality can be useful to allow LVA to listen to wake words even when audio is playing, particularly when a timer is playing. PulseAudio and PipeWire already provide built-in modules for AEC.

## Enabling AEC

### PulseAudio
```sh
pactl load-module module-echo-cancel source_name=aec_mic aec_method=webrtc
```

To make permanent, add to `/etc/pulse/default.pa`:
```
load-module module-echo-cancel source_name=aec_mic aec_method=webrtc
```

### PipeWire
```sh
pw-cli load-module libpipewire-module-echo-cancel '{ source.props={ node.name=aec_mic } }'
```

To make permanent, add the following to the `context.modules` section of `/etc/pipewire/pipewire.conf`:
```
{ name = libpipewire-module-echo-cancel
    args = {
      source.props = { node.name = aec_mic }
    }
}
```

Or if you are using a standard PipeWire installation without a custom `pipewire.conf`, create a new file inside `pipewire.conf.d/`:
```
context.modules = [
  { name = libpipewire-module-echo-cancel
    args = {
      source.props = { node.name = aec_mic }
    }
  }
]
```

## Using the AEC Input

In the `.env` file:
```
AUDIO_INPUT_DEVICE="aec_mic"
```

## Changing Source and Sink for AEC

### PulseAudio
```sh
pactl load-module module-echo-cancel source_name=aec_mic aec_method=webrtc source_master=<mic-source> sink_master=<sink>
```

### PipeWire
```sh
pw-cli load-module libpipewire-module-echo-cancel '{ source.props={ node.name=aec_mic node.description="Echo Cancelled Mic" } sink.props={ node.name=aec_sink } capture.props={ node.name=<your-mic-node-name> } playback.props={ node.name=<your-speaker-node-name> } }'
```

To make permanent, add to `context.modules` in `/etc/pipewire/pipewire.conf` or a file in `pipewire.conf.d/`:
```
{ name = libpipewire-module-echo-cancel
    args = {
      source.props = {
        node.name = aec_mic
        node.description = "Echo Cancelled Mic"
      }
      sink.props = {
        node.name = aec_sink
      }
      capture.props = {
        node.name = <your-mic-node-name>
      }
      playback.props = {
        node.name = <your-speaker-node-name>
      }
    }
}
```

</details>

# Feature: Listen During Wake Sound

<details>
<summary><b>Show details</b></summary>

By default, LVA waits until the wake sound has finished playing before it starts listening for your spoken command. This can force a short pause between saying the wake word and the rest of your request. For example:  `Okay Nabu <pause until wake sound finishes> turn on the kitchen lights`.

Enable `LISTEN_DURING_WAKE_SOUND="1"` to let LVA begin listening immediately after the wake word is detected, even while the wake sound is still playing. This allows you to say the wake word and the full command in one go.

This feature is especially useful when you want a more natural interaction flow and do not want to wait to confirm that the wake word was detected before speaking the command.

Keep in mind that listening while the wake sound is playing can also make the microphone pick up some of that wake sound as echo, depending on your speaker and microphone setup. This echo can interfere with speech-to-text and make commands less accurate. If you run into this, see [Enabling Acoustic Echo Cancellation (AEC)](#acoustic-echo-cancellation).

</details>

# Feature: API Encryption

<details>
<summary><b>Show details</b></summary>

By default the connection between LVA and Home Assistant is plaintext, like an ESPHome device without `api: encryption:`. Microphone audio, replies and controls then travel unencrypted, and anyone who can reach the port can drive the satellite. Set `ENABLE_ENCRYPTION="1"` to turn on the same Noise encryption that ESPHome devices use.

**The key is created on the LVA side, not by Home Assistant.** Home Assistant only asks for it when adding the device and stores it. Both sides need the same key, and nothing is exchanged automatically. The key can only be given as a file, never on the command line, where it would be visible in the process list.

1. Set `ENABLE_ENCRYPTION="1"` and start LVA. If the key file does not exist, LVA creates it (32 random bytes, base64, mode 600). By default it is `encryption.key` in the same directory as the preferences file (`/app/configuration/encryption.key` in Docker). Set `ENCRYPTION_KEY_FILE` to use another path. Docker: the path is inside the container, so keep it in a mounted volume, otherwise a new key is created on every container start. You can also create the key yourself: `openssl rand -base64 32 > encryption.key`.
2. When LVA creates the file it logs a warning with its path (look for "Created encryption key file"). The key itself is never written to the log, so show it from the file and enter it in Home Assistant when it asks (see [Connecting to Home Assistant](installation_part2_application.md#c-connecting-to-home-assistant)):
   ``` sh
   docker compose exec linux-voice-assistant cat /app/configuration/encryption.key   # Docker, use your service name
   cat /home/pi/linux-voice-assistant/encryption.key                                 # systemd
   ```

LVA warns at startup if the key file is readable by other users; fix it with `chmod 600 <file>`. LVA refuses to start if the file cannot be read or is not 32 bytes of base64. Clients without the key, or with a different one, are refused.

To change the key, replace the content of the file, restart LVA, and enter the new key in Home Assistant (ESPHome integration, "Reconfigure" on the device, or remove and re-add it). Remove `ENABLE_ENCRYPTION` to serve plaintext again.

</details>

# Use own soundfiles

<details>
<summary><b>Show details</b></summary>

If you want to use your own sounds, you can add them to the `sounds/custom` aka `/var/lib/docker/volumes/lva_sounds_custom/_data` directory and reference them in the `.env` file.

</details>

# Wake Word

<details>
<summary><b>Show details</b></summary>

💡 **Note:** The active wake word(s) can also be changed at runtime from the Home Assistant device page, without editing `.env` or restarting LVA — see [Wake Word](configuration.md#wake-word) in the dashboard documentation.

## Available Wake Word Models

The following wake word models are available:

**microWakeWord:**
- `okay_nabu` - Default wake word
- `alexa` - Alexa wake word
- `hey_jarvis` - Jarvis wake word
- `hey_mycroft` - Mycroft wake word
- `hey_morgan` - Morgan wake word
- `hey_luna` - Luna wake word
- `hey_home_assistant` - Home Assistant wake word
- `okay_computer` - Okay Computer wake word
- `choo_choo_homie` - Choo Choo Homie wake word

**openWakeWord:**
- `ok_nabu` - Default wake word
- `alexa` - Alexa wake word
- `hey_jarvis` - Jarvis wake word
- `hey_mycroft` - Mycroft wake word
- `hey_rhasspy` - Rhasspy wake word

</details>

# Stop Word

<details>
<summary><b>Show details</b></summary>

The `stop` wake word is used to stop the current process (response, announcement, or timer). It is available in the `wakewords/internal/microwakeword` directory (using the microWakeWord engine) and can be referenced in the `.env` file with `STOP_MODEL=stop`.

</details>

# Wake Word Directories

<details>
<summary><b>Show details</b></summary>

All wake word models live below one root directory, set with `WAKE_WORD_ROOT_DIR` (`--wake-word-root-dir`, default `wakewords/`, in Docker `/app/wakewords`). Below it, models are sorted by origin and by engine:

```text
wakewords/
├── internal/        bundled with LVA, not changed by you
│   ├── microwakeword/
│   └── openwakeword/
├── downloaded/      wake words that Home Assistant sends to the satellite
│   ├── microwakeword/
│   └── openwakeword/
└── custom/          your own wake words
    ├── microwakeword/
    └── openwakeword/
```

`downloaded` and `custom` are created at startup. In Docker they are volumes (`wakeword_downloaded` and `wakeword_custom`), `internal` is part of the image. The engine of a model is taken from the `type` in its `.json` file, the engine directory is only for keeping things tidy.

Every wake word has a name made of origin, engine and the phrase from its `.json` file, for example `Internal - MWW - Hey Jarvis` or `Custom - OWW - Hey Jarvis`. The same model in two directories is two separate wake words that you can choose from in Home Assistant. `WAKE_MODEL` and `WAKE_MODEL_2` accept the file name (`hey_jarvis`) or this name. If the file name exists in more than one origin, the directories are searched in the order `internal`, `downloaded`, `custom` and the last one is used, so a model in `custom` is chosen before a bundled one. The same applies to the stop word. The name is also the phrase LVA sends to Home Assistant with a voice request.

In the wake word selection in Home Assistant, the name of a wake word shows where it comes from and which engine it uses, for example `Internal - MWW - Hey Jarvis`, `Download - OWW - Hey Jarvis` or `Custom - MWW - Hey Jarvis` (MWW = microWakeWord, OWW = openWakeWord). The phrase that is sent to Home Assistant when the wake word is detected is the plain phrase from the `.json` file, for example `Hey Jarvis`.

</details>

## Wake words from Home Assistant

Home Assistant can hand microWakeWord models to the satellite, so you can keep your own wake words in one place:

1. Put the model and its config into the folder `custom_wake_words` of your Home Assistant configuration (next to `configuration.yaml`), for example `custom_wake_words/hey_test.json` and `custom_wake_words/hey_test.tflite`. The `.json` file needs `"type": "micro"` and a `"wake_word"`; openWakeWord models are not supported here.
2. **Restart Home Assistant.** Home Assistant reads this folder only once and keeps the result until it restarts, so a model added or changed later is not offered before that.
3. The wake word now appears in the wake word selection of the satellite. When you choose it, LVA downloads it into `downloaded/microwakeword` in the background and activates it as soon as the download has finished. Until then the previous wake words stay active.

With `--debug`, LVA logs every wake word Home Assistant offers (`External wake word from Home Assistant: …`) and why it is offered or not, and each download step. A failed download is logged as a warning.

# Custom Wake Word

<details>
<summary><b>Show details</b></summary>

If you want to use your own wakewords, you can add them to `wakewords/custom/microwakeword` (microWakeWord models) or `wakewords/custom/openwakeword` (openWakeWord models). Restart LVA after adding files.

With Docker, `wakewords/custom` is the volume `wakeword_custom`. Docker stores it on the host, for example in `/var/lib/docker/volumes/lva_wakeword_custom/_data` (`lva` is the name of the compose project, check it with `docker volume ls`). The folders `microwakeword` and `openwakeword` are created there when LVA starts. Copy your files into one of them, for example:

```sh
sudo cp glados.json glados.tflite /var/lib/docker/volumes/lva_wakeword_custom/_data/openwakeword/
```

LVA does not run as root in the container, so the files must be readable by the user from `LVA_USER_ID` and `LVA_USER_GROUP` (default 1000:1000). The `fix-permissions` service in the compose file does this on every `docker compose up`. If you copied files as root and LVA does not find them, run `docker compose up -d` again or set the owner by hand:

```sh
sudo chown -R 1000:1000 /var/lib/docker/volumes/lva_wakeword_custom/_data
```

Change the default wake word with `WAKE_MODEL <id>` where `<id>` is the name of a model in one of the wake word directories. For example, `WAKE_MODEL hey_jarvis` will load `wakewords/internal/microwakeword/hey_jarvis.tflite` by default, or the model with that name from `custom` if you have one.

Set `WAKE_MODEL_2 <id>` to also activate a second wake word by default. It is looked up like `WAKE_MODEL`; if it is empty, unknown or the same as the first one, only the first wake word is active. Both defaults apply only while no wake words are stored yet (first start, or after the preferences file was removed). After that, the wake words chosen in Home Assistant are kept and `WAKE_MODEL` / `WAKE_MODEL_2` are ignored.

If you want to add [other wakewords][wakewords-collection], make sure to create a small JSON config file to identify it as an openWakeWord model. For example, download the [GLaDOS][glados] model to `glados.tflite` and create `glados.json` with:

``` json
{
  "type": "openWakeWord",
  "wake_word": "GLaDOS",
  "model": "glados.tflite"
}
```

Put `glados.tflite` and `glados.json` into `wakewords/custom/openwakeword`.

## Additional Wakewords

More wakewords can be found in the [wakewords-collection](https://github.com/fwartner/home-assistant-wakewords-collection) repository.

</details>
