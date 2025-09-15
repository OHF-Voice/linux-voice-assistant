## Install PulseAudio On Your Wyoming Voice Satellite

> Courtesy of https://github.com/FutureProofHomes/wyoming-enhancements/blob/master/snapcast/docs/2_install_pulseaudio.md with minor modifications

## 1. Connect to your Pi over SSH using the username/password you configured during flashing:

```sh
ssh <your_username>@<pi_IP_address>
```


## 2. If already installed Disable/Stop the entire the Linux Voice Assistant service temporarily. If not installed, proceed to step (3.):

```sh
sudo systemctl disable --now linux-voice-assistant.service
```


## 3. Install PulseAudio drivers and necessary utilities:

```sh
sudo apt-get update
sudo apt-get install \
    pulseaudio \
    pulseaudio-utils \
    pamixer
```


## 4. Reboot your Pi:

```sh
sudo reboot -h now
```


## 5. SSH back in to your Pi.  (See step 1.)


## 6. Ensure PulseAudio is runnining in "system-wide mode".  Per the [official instructions](https://www.freedesktop.org/wiki/Software/PulseAudio/Documentation/User/SystemWide/) we first need to stop some existing services to be safe:

```sh
sudo systemctl --global disable pulseaudio.service pulseaudio.socket
```


## 7. It is also advisable to set `autospawn = no` in `/etc/pulse/client.conf`:

```sh
sudo sed -i.bak \
    -e '$a### Disable autospawn' \
    -e '$aautospawn = no' \
    /etc/pulse/client.conf
```


## 8. Create the `PulseAudio.service`:

```sh
sudo cp ~/linux-voice-assistant/service/pulseaudio.service /etc/systemd/system/pulseaudio.service
```


## 9. Start the `PulseAudio.service`.  This will require you to type in your password a couple times.

```sh
systemctl --system enable pulseaudio.service
```

```sh
systemctl --system start pulseaudio.service
```


## 10. Ensure pi user is in the `pulse-access` group:
***If you aren't using the default "pi" user, put your user in here instead of "pi".***
```sh
sudo sed -i '/^pulse-access:/ s/$/root,pi/' /etc/group
```


## 11. Reboot your Pi: (see step 4)


## 12. (OPTIONAL) If plugging speakers into the headphone jack of the ReSpeaker, set the correct PulseAudio Sink Port:

Run `pactl list sinks` and scroll to the bottom and notice your Active Port is probably "analog-output-speaker".  Run the command below to output audio through the 3.5mm headphone jack.

```sh
pactl set-sink-port 1 "analog-output-headphones"
```

**Please note that if you are setting this up on a Raspberry Pi 3B or 4B, you need an additional step at this point to make it route the audio through the 2mic hat (and likely the 4mic one as well). Run the following command to do this.**

```sh
pactl set-default-sink alsa_output.platform-soc_sound.stereo-fallback
```


## 13. Test and make sure you can hear the wav file.:

```sh
paplay /usr/share/sounds/alsa/Front_Center.wav
```

**Note that if you have a Raspbery Pi 3B or 4B, this may or may not seem to work, but it will output on the hat as intended.**


## 14. The following modifications are specifically required for LVA's mpv_player.py to work with PA. If you do not have a working PA at this point, do not proceed.:
Backup current PA daemon.conf file.

```bash
sudo cp /etc/pulse/daemon.conf{,.bak.$(date +%s)}
```

Update the PA /etc/pulse/daemon.conf file with required change. Replaces if present (commented or not).

```bash
sudo sed -i -E \
  -e 's|^[#;[:space:]]*exit-idle-time\s*=.*$|exit-idle-time = -1|' \
  -e 's|^[#;[:space:]]*resample-method\s*=.*$|resample-method = trivial|' \
  -e 's|^[#;[:space:]]*flat-volumes\s*=.*$|flat-volumes = no|' \
  -e 's|^[#;[:space:]]*default-sample-rate\s*=.*$|default-sample-rate = 44100|' \
  -e 's|^[#;[:space:]]*alternate-sample-rate\s*=.*$|alternate-sample-rate = 48000|' \
  -e 's|^[#;[:space:]]*default-fragment-size-msec\s*=.*$|default-fragment-size-msec = 15|' \
  /etc/pulse/daemon.conf
```

Just in case the values were missing, may not be needed but shouldn't hurt.

```bash
for kv in \
 'exit-idle-time = -1' \
 'resample-method = trivial' \
 'flat-volumes = no' \
 'default-sample-rate = 44100' \
 'alternate-sample-rate = 48000' \
 'default-fragment-size-msec = 15'; do
  k="${kv%% =*}"
  sudo grep -Eq "^[[:space:]]*${k}[[:space:]]*=" /etc/pulse/daemon.conf || echo "$kv" | sudo tee -a /etc/pulse/daemon.conf >/dev/null
done
```

Backup current PA system.pa file.

```bash
sudo cp /etc/pulse/system.pa{,.bak.$(date +%s)}
```

Update the PA /etc/pulse/system.pa file with required change. Replaces if present (commented or not).

```bash
sudo sed -i -E \
  -e 's|^[#;[:space:]]*(load-module[[:space:]]+module-udev-detect)([[:space:]].*)?$|\1 tsched=0|' \
  -e 's|^[#;[:space:]]*(load-module[[:space:]]+module-native-protocol-unix.*)$|\1|' \
  -e 's|^[#;[:space:]]*(load-module[[:space:]]+module-default-device-restore.*)$|\1|' \
  -e 's|^[#;[:space:]]*(load-module[[:space:]]+module-always-sink.*)$|\1|' \
  -e 's|^[#;[:space:]]*(load-module[[:space:]]+module-position-event-sounds.*)$|\1|' \
  /etc/pulse/system.pa
```

Just in case the values were missing, may not be needed but shouldn't hurt.

```bash
for m in \
 'load-module module-native-protocol-unix' \
 'load-module module-default-device-restore' \
 'load-module module-always-sink' \
 'load-module module-position-event-sounds'; do
  sudo grep -Eq "^[[:space:]]*$m(\s|$)" /etc/pulse/system.pa || echo "$m" | sudo tee -a /etc/pulse/system.pa >/dev/null
done
```

Backup current PA default.pa file.

```bash
sudo cp /etc/pulse/default.pa{,.bak.$(date +%s)}
```

Update the PA /etc/pulse/default.pa file with required change. Replaces if present (commented or not).

```bash
sudo sed -i -E \
  -e 's|^[#;[:space:]]*(load-module[[:space:]]+module-udev-detect)([[:space:]].*)?$|\1 tsched=0|' \
  -e 's|^[#;[:space:]]*(load-module[[:space:]]+module-native-protocol-unix.*)$|\1|' \
  -e 's|^[#;[:space:]]*(load-module[[:space:]]+module-default-device-restore.*)$|\1|' \
  -e 's|^[#;[:space:]]*(load-module[[:space:]]+module-always-sink.*)$|\1|' \
  -e 's|^[#;[:space:]]*(load-module[[:space:]]+module-intended-roles.*)$|\1|' \
  /etc/pulse/default.pa
```

Just in case the values were missing, may not be needed but shouldn't hurt.

```bash
for m in \
 'load-module module-native-protocol-unix' \
 'load-module module-default-device-restore' \
 'load-module module-always-sink' \
 'load-module module-intended-roles'; do
  sudo grep -Eq "^[[:space:]]*$m(\s|$)" /etc/pulse/default.pa || echo "$m" | sudo tee -a /etc/pulse/default.pa >/dev/null
done
```

Optional since LVA handles duck/unduck: if you enabled role-ducking explicitly in system.pa, you can comment it out with:

```bash
sudo sed -i -E 's|^([[:space:]]*)load-module[[:space:]]+module-role-ducking\b|# \0|' /etc/pulse/system.pa
```
## 15. Restart PA and verify

```bash
sudo systemctl restart pulseaudio.service
```

Test mpv can ouput to PA with current configs:

```bash
mpv --ao=pulse --audio-device=default --audio-samplerate=44100 /usr/share/sounds/alsa/Front_Center.wav
```

## 16. Reboot your Pi: (see step 4)


## 17. Done! Return to the tutorial to continue the Linux Voice Assistant install.








