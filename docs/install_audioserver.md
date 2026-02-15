# Install Audioservice

For Linux-Voice-Assistant a Pulseaudio connection to the soundcard is required. Since PulseAudio is not installed by default on Ubuntu 22.04 we also support Pipewire. You can choose either one (A or B) of them.

## A) Pipewire (recommended):

PipeWire is a multimedia server that provides low-latency audio/video handling. Install it with the following commands:

``` sh
# Update package database
sudo apt update

# Install PipeWire and related packages
sudo apt install -y pipewire wireplumber pipewire-audio-client-libraries libspa-0.2-bluetooth pipewire-audio pipewire-pulse dfu-util
```

Link the PipeWire configuration for ALSA applications:

``` sh
sudo ln -s /usr/share/alsa/alsa.conf.d/50-pipewire.conf /etc/alsa/conf.d/
```

Allow services to run without an active user session (optional, for headless setups):

``` sh
sudo mkdir -p /var/lib/systemd/linger
sudo touch /var/lib/systemd/linger/$USER
```

💡 **Note:** Replace `$USER` with your actual username that you want to run the voice assistant.

### Configure PipeWire (optional):

Create the PipeWire configuration directory:

``` sh
sudo mkdir -p "/etc/pipewire"
```

Create the file `/etc/pipewire/pipewire.conf` with the following content (minimal configuration for voice assistant use):

``` ini
# Daemon config file for PipeWire
context.properties = {
    link.max-buffers = 16
    mem.warn-mlock = true
    log.level = 3
    context.num-data-loops = 1
    core.daemon = true
    core.name = pipewire-0
    default.clock.rate = 16000
}

context.modules = [
    { name = libpipewire-module-rt
        args = {
            nice.level = -11
            rt.prio = 88
        }
        flags = [ ifexists nofail ]
    }
    { name = libpipewire-module-protocol-native }
    { name = libpipewire-module-profiler }
    { name = libpipewire-module-metadata }
    { name = libpipewire-module-spa-device-factory }
    { name = libpipewire-module-spa-node-factory }
    { name = libpipewire-module-client-node }
    { name = libpipewire-module-client-device }
    { name = libpipewire-module-portal
        flags = [ ifexists nofail ]
    }
    { name = libpipewire-module-access
        condition = [ { module.access = true } ]
    }
    { name = libpipewire-module-adapter }
    { name = libpipewire-module-link-factory }
    { name = libpipewire-module-session-manager }
]
```


## B) PulseAudio:

Make sure that you only run Pulseaudio and there is no Pipewire installed.

``` sh
sudo apt remove --purge pipewire pipewire-pulse wireplumber
sudo apt autoremove
```

Install Pulseaudio

``` sh
sudo apt install pulseaudio pulseaudio-utils dfu-util
```

Enable and start Pulseaudio

``` sh
systemctl --user enable pulseaudio
systemctl --user start pulseaudio
```

Check if Pulseaudio is running

``` sh
pulseaudio --check
pactl info
```


## Additional Information:

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
