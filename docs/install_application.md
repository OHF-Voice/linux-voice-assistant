# Install Application

You can install the application in different ways. We recommend to use Docker Compose if not the prebuilt image. But if you dont want to use Docker you can also install it directly on your system.

## A) Docker Compose (recommended):

Install packages:
``` sh
sudo apt-get install -y ca-certificates curl wget gnupg lsb-release git jq vim
```

Download and add Docker's official GPG key:
``` sh
mkdir -p /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/debian/gpg | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
sudo chmod a+r /etc/apt/keyrings/docker.gpg
```

Set up the Docker repository:
``` sh
echo \
  "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \
  https://download.docker.com/linux/debian \
  $(lsb_release -cs) stable" | \
  sudo tee /etc/apt/sources.list.d/docker.list > /dev/null
```

Install Docker and Docker Compose:
``` sh
sudo apt-get update
sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
```

Download the docker-compose.yml and .env.example file from the repository to a folder on your system:
``` sh
mkdir linux-voice-assistant
cd linux-voice-assistant
LVA_VERSION=$(curl -s https://api.github.com/repos/ohf-voice/linux-voice-assistant/releases/latest | jq -r .tag_name)
echo "Installing version: " $LVA_VERSION
wget https://raw.githubusercontent.com/ohf-voice/linux-voice-assistant/refs/tags/$LVA_VERSION/docker-compose.yml
wget https://raw.githubusercontent.com/ohf-voice/linux-voice-assistant/refs/tags/$LVA_VERSION/.env.example
mv .env.example .env
```

💡 **Note:** The LVA_VERSION variable downloads the latest available version of LVA. If you want another version, you can specify it with the LVA_VERSION variable.

### Docker Image Tags

| Tag | Description | Example |
|-----|-------------|---------|
| `latest` | Latest stable release | `ghcr.io/ohf-voice/linux-voice-assistant:latest` |
| `nightly` | Latest development build | `ghcr.io/ohf-voice/linux-voice-assistant:nightly` |
| `x.y.z` | Specific version release | `ghcr.io/ohf-voice/linux-voice-assistant:1.0.0` |
| `x.y` | Major.Minor with auto updates | `ghcr.io/ohf-voice/linux-voice-assistant:1.0` |
| `<branch>` | Branch-specific build | `ghcr.io/ohf-voice/linux-voice-assistant:my-branch` |

Edit the .env file and change the values to your needs:
``` sh
vim .env
```

```ini
# Linux-Voice-Assistant - Docker Environment Configuration
# Copy this file to .env and customize for your setup by 'cp .env.example .env'

### Enable debug mode (optional):
# ENABLE_DEBUG="1"

### List audio devices (optional):
# if enabled normal startup is disabled
# LIST_DEVICES="1"

### User ID:
# This is used to set the correct permissions for the accessing the audio device and accessing the PulseAudio socket
LVA_USER_ID="1000"
LVA_USER_GROUP="1000"

### Name for the client (optional):
# by default it uses the HOSTNAME variable from the piCompose environment which includes the MAC from the network card
# CLIENT_NAME="My Voice Assistant Speaker"

### PulseAudio socket path on the host:
# PulseAudio Server:    /run/user/1000/pulse
# Pipewire Server:      /run/user/1000/pulse/native
LVA_PULSE_SERVER="unix:/run/user/${LVA_USER_ID}/pulse/native"
LVA_XDG_RUNTIME_DIR="/run/user/${LVA_USER_ID}"

### Path to the preferences file (optional):
# PREFERENCES_FILE="/app/configuration/preferences.json"

### Port for the api (optional):
# PORT="6053"

### Audio input device (optional):

[...]
```

Please use the original file from the repository. You can change various settings, for example the audio sounds which are played when the wake word is detected or when the timer is finished.

💡 **Note:** You can exit vim with `:wq` or `:q!` if you dont want to save the changes.

Start the application:
``` sh
docker compose up -d
```

💡 **Note:** If you want to use the application with a different user, you need to change the user in the .env file. Dont forget to change the UID from the user. The docker container will run until you stop it. It will restart autiomatically after a reboot.

Check if the application is running:
``` sh
docker compose ps
```

Check the logs:
``` sh
docker compose logs -f
```

Stop the service:
``` sh
docker-compose down
```

Download the latest image:
``` sh
docker-compose pull
```


## B) Bare Metal:

Install the required packages for the application:

``` sh
sudo apt update
sudo apt-get install \
  avahi-utils \
  pulseaudio-utils \
  alsa-utils \
  pipewire-bin \
  pipewire-alsa \
  pipewire-pulse \
  build-essential \
  libmpv-dev \
  libasound2-plugins \
  ca-certificates \
  iproute2 \
  procps \
  git \
  jq \
  curl \
  wget \
  vim \
  python3-venv \
  python3-dev
```

Clone the repository:

``` sh
git clone https://github.com/OHF-Voice/linux-voice-assistant.git
cd linux-voice-assistant
chmod +x docker-entrypoint.sh
```

Install the application:

``` sh
script/setup
```

Create a systemd service for the application:

``` sh
sudo systemctl edit --force --full linux-voice-assistant.service
```

Paste the following content into the file:

``` ini
[Unit]
Description=Linux-Voice-Assistant
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=pi
WorkingDirectory=/home/pi/linux-voice-assistant
Environment=PATH=/home/pi/linux-voice-assistant/.venv/bin:/usr/bin:/bin
# Environment=ENABLE_DEBUG="1"
# Environment=LIST_DEVICES="1"
# Environment=CLIENT_NAME="My Voice Assistant Speaker"
Environment=LVA_PULSE_SERVER="unix:/run/user/${LVA_USER_ID}/pulse/native"
Environment=LVA_XDG_RUNTIME_DIR="/run/user/${LVA_USER_ID}"
Environment=PREFERENCES_FILE="/home/pi/linux-voice-assistant/preferences.json"
# Environment=NETWORK_INTERFACE="eth0"
# Environment=HOST="0.0.0.0"
# Environment=PORT="6053"
# Environment=AUDIO_INPUT_DEVICE="default"
# Environment=AUDIO_OUTPUT_DEVICE="default"
# Environment=ENABLE_THINKING_SOUND="1"
# Environment=WAKE-MODEL="okay_nabu"
# Environment=REFACTORY_SECONDS="2"
# Environment=WAKEUP_SOUND="sounds/wake_word_triggered.flac"
# Environment=TIMER_FINISHED_SOUND="sounds/timer_finished.flac"
# Environment=PROCESSING_SOUND="sounds/processing.wav"
# Environment=MUTE_SOUND="sounds/mute_switch_on.flac"
# Environment=UNMUTE_SOUND="sounds/mute_switch_off.flac"
ExecStart=/home/pi/linux-voice-assistant/docker-entrypoint.sh
# ExecStart=/home/pi/linux-voice-assistant/docker-entrypoint.sh --additional-parameter-if-you-want
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

💡 **Note:** We are using the `docker-entrypoint.sh` script to start the application. This script is located in the root of the repository. But there is no docker used. Only the start script is used.

💡 **Note:** Replace `pi` with your actual user that you want to run the voice assistant. You need to run Pipewire and LVA with the same user in order to provide access to the audio socket. You can also add the group audio to the user which LVA is running on. `sudo usermod -a -G audio pi`

Reload the systemd daemon and start the service:

``` sh
sudo systemctl daemon-reexec
sudo systemctl daemon-reload
sudo systemctl enable linux-voice-assistant
sudo systemctl start linux-voice-assistant
```

💡 **Note:** If you want to change settings you can do this with editing the Environment in the service file.

If you want to check if the service is running you can use the following command:

``` sh
sudo systemctl status linux-voice-assistant
```


## Connecting to Home Assistant:

1. In Home Assistant, go to "Settings" -> "Device & services"
2. Click the "Add integration" button
3. Choose "ESPHome" and then "Set up another instance of ESPHome"
4. Enter the IP address of your voice satellite with port 6053
5. Click "Submit"


## Additional Information:

### Settings:
All optional settings except the audio sounds are disabled by default. You can change them in the UI and they will be saved in the PREFERENCES_FILE. If you set the configuration in the service file, the corresponding settings will be overwritten and you are no longer able to change them in the UI.

If you want to change the audio sounds you need to change the paths in the service file. 

#### Environment Variables Reference:

The following variables can be configured in the `.env` or in the service file:

| Variable | Default | Description |
|----------|---------|-------------|
| `LVA_USER_ID` | `1000` | User ID for the container (usually 1000 for the first user) |
| `LVA_USER_GROUP` | `1000` | GROUP ID for the container (usually 1000 for the first users group) |
| `CLIENT_NAME` | (optional) | Custom name for this voice assistant instance |
| `LVA_PULSE_SERVER` | `unix:/run/user/${LVA_USER_ID}/pulse/native` | Path to the PulseAudio/PipeWire socket |
| `LVA_XDG_RUNTIME_DIR` | `/run/user/${LVA_USER_ID}` | XDG runtime directory |
| `ENABLE_DEBUG` | (optional) | Set to "1" to enable debug mode |
| `LIST_DEVICES` | (optional) | Set to "1" to list audio devices instead of starting |
| `PREFERENCES_FILE` | (optional) | Path to a custom preferences JSON file |
| `NETWORK_INTERFACE` | Autodetected | network card for server |
| `HOST` | Autodetected | API server IP-Address, can be 0.0.0.0 for all interfaces, but only one network card works for MAC-ADDRESS and ESP protocol |
| `PORT` | `6053` | API server port |
| `AUDIO_INPUT_DEVICE` | `default` | Audio input device name |
| `AUDIO_OUTPUT_DEVICE` | `default` | Audio output device name |
| `ENABLE_THINKING_SOUND` | false | Set to "1" to enable thinking sound |
| `WAKE_MODEL` | `okay_nabu` | Wake word model to use |
| `REFACTORY_SECONDS` | `2` | Refractory period in seconds after wake word |
| `WAKEUP_SOUND` | `sounds/wake_word_triggered.flac` | Sound file for wake word triggered |
| `TIMER_FINISHED_SOUND` | `sounds/timer_finished.flac` | Sound file for timer finished |
| `PROCESSING_SOUND` | `sounds/processing.wav` | Sound file for processing state |
| `MUTE_SOUND` | `sounds/mute_switch_on.flac` | Sound file for mute on |
| `UNMUTE_SOUND` | `sounds/mute_switch_off.flac` | Sound file for Configure Audio Devices

#### Available Wake Word Models:

The following wake word models are available:
- `okay_nabu` - Default wake word
- `alexa` - Alexa wake word
- `hey_jarvis` - Jarvis wake word
- `hey_mycroft` - Mycroft wake word
- `hey_luna` - Luna wake word
- `hey_home_assistant` - Home Assistant wake word
- `stop` - Stop wake word
- `okay_computer` - Okay Computer wake word
- `choo_choo_homie` - Choo Choo Homie wake word


### Wake Word:

💡 **Note:** The default value for this parameter is the `wakewords` directory in the repository.

Change the default wake word with `--wake-model <id>` where `<id>` is the name of a model in the `wakewords` directory. For example, `--wake-model hey_jarvis` will load `wakewords/hey_jarvis.tflite` by default.

You can include more wakeword directories by adding `--wake-word-dir <DIR>` where `<DIR>` contains either [microWakeWord][] or [openWakeWord][] config files and `.tflite` models. For example, `--wake-word-dir wakewords/openWakeWord` will include the default wake words for openWakeWord.

If you want to add [other wakewords][wakewords-collection], make sure to create a small JSON config file to identify it as an openWakeWord model. For example, download the [GLaDOS][glados] model to `glados.tflite` and create `glados.json` with:

``` json
{
  "type": "openWakeWord",
  "wake_word": "GLaDOS",
  "model": "glados.tflite"
}
```

Add `--wake-word-dir <DIR>` with the directory containing `glados.tflite` and `glados.json` to your command-line.

#### Additional Wakewords:

More wakewords can be found in the [wakewords-collection](https://github.com/fwartner/home-assistant-wakewords-collection) repository.
