# Install Audioservice

For Linux-Voice-Assistant a Pulseaudio connection to the soundcard is required. Since PulseAudio is no longer the default audio-server we recommend to use and install pipewire (pipewire-pulse) by default. We also support PulseAudio. You can choose either variant A or B from this installation documentation.

**How does the communication work?**

The Linux-Voice-Assistant container connects to the PulseAudio(PipeWire-Pulse) server running on the host system. The container does not have direct access to the sound card hardware. Instead, it communicates with the PulseAudio running on the host system, which then handles the actual audio processing and output.

## A) Pipewire (recommended):

PipeWire is a multimedia server that provides low-latency audio/video handling. 

#### Install packages:

Install it with the following commands:

```sh
# Update package database
sudo apt update

# Install PipeWire and related packages
sudo apt install -y pipewire wireplumber pipewire-audio-client-libraries libspa-0.2-bluetooth pipewire-audio pipewire-pulse dfu-util pulseaudio-utils
```

#### Configure Alsa:

Link the PipeWire configuration for ALSA applications:

```sh
sudo ln -sf /usr/share/alsa/alsa.conf.d/50-pipewire.conf /etc/alsa/conf.d/
```

#### Setup linger:

Allow services to run without an active user session. This step is **required on headless systems** (e.g. Raspberry Pi OS Lite), since PipeWire runs under the user's systemd session and only starts while someone is logged in. Without it, LVA will silently stop working after you close your SSH session.

```sh
sudo mkdir -p /var/lib/systemd/linger
sudo touch /var/lib/systemd/linger/$USER
```

💡 **Note:** Replace `$USER` with your actual username that you want to run the voice assistant.

You can verify it worked with:

```sh
loginctl show-user $USER | grep Linger
# Expected: Linger=yes
```

#### Configure WirePlumber - output devices:

💡 **Note:** WirePlumber is the session manager of PipeWire. It decides how sound cards are set up, for example which volume they start with and when they are put to sleep. The following two files make sure that the output device starts at 100 % volume, so that LVA's own volume control (and not a low stored default) decides how loud it is, and that the output device is not suspended when it is idle.

- `audio.volume = 1.0` sets the volume of every `alsa_output.*` device to 100 %.
- `session.suspend-timeout-seconds = 0` and `node.pause-on-idle = false` keep the output device open instead of suspending it when nothing is playing. This should avoid that the start of a sound is cut off while the device wakes up. This effect is not verified.
- `device.routes.default-sink-volume = 1.0` sets the volume that WirePlumber gives a sound card that has no stored state. A state stored in `~/.local/state/wireplumber` wins over it, so it does not change a card that was already set up.

```sh
sudo mkdir -p /etc/wireplumber/wireplumber.conf.d
sudo tee /etc/wireplumber/wireplumber.conf.d/50-alsa-output.conf > /dev/null << 'EOF'
monitor.alsa.rules = [
    {
        matches = [
            {
                node.name = "~alsa_output.*"
            }
        ]
        actions = {
            update-props = {
                audio.volume = 1.0
                session.suspend-timeout-seconds = 0
                node.pause-on-idle = false
            }
        }
    }
]
EOF
sudo tee /etc/wireplumber/wireplumber.conf.d/51-default-volume.conf > /dev/null << 'EOF'
wireplumber.settings = {
    device.routes.default-sink-volume = 1.0
}
EOF
```

#### Configure PipeWire - process priority:

💡 **Note:** On constrained hardware like a Raspberry Pi 3B, audio
processing can be interrupted by other system activity, causing
audible glitches or dropped wake-word detections. Loading the
`libpipewire-module-rt` module raises PipeWire's scheduling priority
(`rt.prio`) above normal processes, which reduces these interruptions.
The `flags = [ ifexists nofail ]` ensures this configuration is
silently skipped rather than causing a startup failure on systems
where realtime scheduling isn't available (e.g. some containers or
restricted kernels).

```sh
sudo mkdir -p /etc/pipewire/pipewire.conf.d
sudo tee /etc/pipewire/pipewire.conf.d/linux-voice-assistant.conf > /dev/null << 'EOF'
context.modules = [
    {   name = libpipewire-module-rt
        args = {
            nice.level    = -11
            rt.prio       = 88
        }
        flags = [ ifexists nofail ]
    }
]
EOF
```

LVA is meant to be configured system wide. So only that method has been documented.

### Configure PipeWire - Clock-Rate fix (optional):

💡 **Note:** LVA records audio at 16kHz. By default PipeWire may run at a different sample rate (typically 48kHz) and will resample automatically. Setting `default.clock.rate = 16000` (optional, see below) avoids this resampling overhead, which is particularly beneficial on low-power hardware such as a Raspberry Pi.

💡 **Note:** Some devices have a problem with PipeWire's default clock rate (48000). The Jabra SPEAK 510, for example, is a USB conference device with restricted sample rates. The rate PipeWire negotiates probably does not match the device cleanly, and the result sounds wrong, for example with a shifted pitch or speed (see [issue #233](https://github.com/OHF-Voice/linux-voice-assistant/issues/233)). Explicitly setting `default.clock.rate` forces PipeWire onto a rate the device can handle cleanly. This cause is not verified.

The rate is not part of the default configuration above. If you need it, put it into a separate file:

```sh
sudo tee /etc/pipewire/pipewire.conf.d/10-clock-rate.conf > /dev/null << 'EOF'
context.properties = {
    default.clock.rate = 16000
}
EOF
```

### Applying the Changes
PipeWire runs as a systemd user service under the user session (e.g., UID 1000). When executing commands as root, systemctl --user cannot connect to the user's DBus session unless the required environment variables are set. But as recommend at the end of this documentation part it is better and we recommend to restart the whole system.

```sh
sudo -u pi XDG_RUNTIME_DIR=/run/user/1000 systemctl --user restart pipewire pipewire-pulse wireplumber
```

💡 **Note:** In certain cases where hardware drivers need to be installed, a system reboot may be required. 

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

Reboot the system:

In some cases to get the audio hardware working you may need to restart your system.



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
