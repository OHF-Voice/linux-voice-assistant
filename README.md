# Linux Voice Assistant

Experimental Linux voice assistant for [Home Assistant][homeassistant] that uses the [ESPHome][esphome] protocol.

Runs on Linux `aarch64` and `x86_64` platforms. Tested with Python 3.13 and Python 3.11.
Supports announcments, start/continue conversation, and timers.

See [the tutorial](docs/linux-voice-assistant-2mic-install.md) to build a satellite using a [Raspberry Pi Zero 2 W](https://www.raspberrypi.com/products/raspberry-pi-zero-2-w/) and a [ReSpeaker 2Mic HAT](https://wiki.keyestudio.com/Ks0314_keyestudio_ReSpeaker_2-Mic_Pi_HAT_V1.0). 

### What's working:
- This fork introduces the ability to use Wyoming OpenWakeWord (OWW) instead of MicroWakeWord (MWW).
- Now supports **both ALSA and PulseAudio** playback backends using the updated `linux_voice_assistant/mpv_player.py`.
- You can choose between ALSA and PulseAudio with either MWW or OWW simply by enabling the matching systemd service file.
- You can change the OWW wake word within HA after the VLA is registered. (defaults to the wake word defind in the systemd config file)

## Installation

Install system dependencies (`apt-get`):

* `libportaudio2` (for `sounddevice`)
* `build-essential` (for `pymicro-features`)
* `libmpv-dev` (for `python-mpv`)
* `mpv` (for testing)
* `libmpv-dev` (for building spidev)

Clone and install project:

``` sh
git clone https://github.com/OHF-Voice/linux-voice-assistant.git
cd linux-voice-assistant
script/setup
```

## Running

Use `script/run` or `python3 -m linux_voice_assistant`

See `--help` for more options.

## Connecting to Home Assistant

1. In Home Assistant, go to "Settings" -> "Device & services"
2. Click the "Add integration" button
3. Choose "ESPHome" and then "Set up another instance of ESPHome"
4. Enter the IP address of your voice satellite with port 6053
5. Click "Submit"


## ToDo:

* Merge jianyu-li's PR from source project to add mute switch function in this branch
* Configure LVA to advertise on Zeroconf/mDNS via Avahi for HA to auto detect (in progress)
* Implement a single LVA systemd unit file that can be addapted using profiles and drop-ins (in progress)
* Implement OWW model validation checks and error handling so that a bad model doesn't crash OWW
* Make the selection of the right ALSA ar PA device more scripted
* Make a Docker of the project
* Add sensor entity that displays which wake word engine is being used for HA
* Could this be an DEB package?
* Implement Stop for OWW
* Stretch goal: create a smart installer full of validation and sanity checks
<!-- Links -->
[homeassistant]: https://www.home-assistant.io/
[esphome]: https://esphome.io/
[wyoming]: https://github.com/rhasspy/wyoming-openwakeword/
[future proof home]: https://github.com/FutureProofHomes/wyoming-enhancements/
