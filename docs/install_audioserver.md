# Install Audioservice

For Linux-Voice-Assistant a Pulseaudio connection to the soundcard is required. Since PulseAudio is not installed by default on Ubuntu 22.04 we also support Pipewire with pipewire-pulse. You can choose either variant A or B from this installation documentation.

**How does the communication work?**

The Linux-Voice-Assistant container connects to the PulseAudio(PipeWire-Pulse) server running on the host system. The container does not have direct access to the sound card hardware. Instead, it communicates with the PulseAudio running on the host system, which then handles the actual audio processing and output.

## A) Pipewire (recommended):

PipeWire is a multimedia server that provides low-latency audio/video handling. Install it with the following commands:

```sh
# Update package database
sudo apt update

# Install PipeWire and related packages
sudo apt install -y pipewire wireplumber pipewire-audio-client-libraries libspa-0.2-bluetooth pipewire-audio pipewire-pulse dfu-util pulseaudio-utils
```

Link the PipeWire configuration for ALSA applications:

```sh
sudo ln -s /usr/share/alsa/alsa.conf.d/50-pipewire.conf /etc/alsa/conf.d/
```

Allow services to run without an active user session (optional, for headless setups):

```sh
sudo mkdir -p /var/lib/systemd/linger
sudo touch /var/lib/systemd/linger/$USER
```

💡 **Note:** Replace `$USER` with your actual username that you want to run the voice assistant.

### Configure PipeWire (optional):

💡 **Note:** If you are not on a desktop system, which is already configured for PipeWire, you can configure it manually.
💡 **Note:** LVA records audio at 16kHz. By default PipeWire may run at a different sample rate (typically 48kHz) and will resample automatically. Setting `default.clock.rate = 16000` avoids this resampling overhead, which is particularly beneficial on low-power hardware such as a Raspberry Pi.

LVA is meant to be configured system wide. So only that method has been documented.

#### System-wide Configuration
```sh
sudo mkdir -p /etc/pipewire/pipewire.conf.d
sudo vi /etc/pipewire/pipewire.conf.d/linux-voice-assistant.conf
```

Add the following content:
```
context.properties = {
    default.clock.rate = 16000
}
```

#### Applying the Changes
```sh
systemctl --user restart pipewire pipewire-pulse wireplumber
```

💡 **Note:** In certain cases where hardware drivers need to be installed, a system reboot may be required. For hardware such as the Seeed 2-Mic Voice Card, multiple reboots may be needed to ensure the driver is installed and loaded correctly.

## B) PulseAudio:

Make sure that you only run Pulseaudio and there is no Pipewire installed.

```sh
sudo apt remove --purge pipewire pipewire-pulse wireplumber
sudo apt autoremove
```

Install Pulseaudio

```sh
sudo apt install pulseaudio pulseaudio-utils dfu-util
```

Configure pulse

```sh
sudo vi /etc/pulse/daemon.conf
```

Change the following lines in the file:

```sh
 default-sample-rate = 16000
```

Enable and start Pulseaudio

```sh
systemctl --user enable pulseaudio
systemctl --user start pulseaudio
```

Check if Pulseaudio is running

```sh
pulseaudio --check
pactl info
```

### Reboot

After you have made the changes, reboot your system to apply the changes. 


## Additional Information:

### Debug audio output:

You can debug the audio output with the following command:

```bash
export LVA_XDG_RUNTIME_DIR=/run/user/${LVA_USER_ID}
sudo aplay -L
speaker-test -D pulse -c2 -twav
```

💡 **Note:** Replace `$LVA_USER_ID` with your actual user id that you want to run the voice assistant.

💡 **Note:** You can replace `pulse` with `default` or `alsa_output.pci-0000_00_1f.3.analog-stereo` or any other audio device.

### Set audio volume:

If your driver or audiodevice is loaded and you can see the device with `aplay -L` then
set the audio volume from 0 to 100:

```bash
export LVA_XDG_RUNTIME_DIR=/run/user/${LVA_USER_ID}
sudo amixer -c seeed2micvoicec set Headphone 100%
sudo amixer -c seeed2micvoicec set Speaker 100%
sudo amixer -c Lite set Headphone 100%
sudo amixer -c Lite set Speaker 100%
sudo alsactl store
```

💡 **Note:** Replace `$LVA_USER_ID` with your actual user id that you want to run the voice assistant.

Alternatively you can use the following command to set the volume:

```bash
export LVA_XDG_RUNTIME_DIR=/run/user/${LVA_USER_ID}
sudo alsamixer
```

💡 **Note:** Replace `$LVA_USER_ID` with your actual user id that you want to run the voice assistant.

## Adding Acoustic Echo Cancellation

Acoustic Echo Cancellation (AEC) is a type of sound processing used to cancel out the noise coming out of your speaker and going into your mic. In LVA this functionality can be useful to allow LVA to listen to wake words even when audio is playing, particularly when a timer is playing. PulseAudio and PipeWire already provide built-in modules for AEC. To enable AEC, see [Linux-Voice-Assistant - Enabling AEC](enabling_aec.md). 
