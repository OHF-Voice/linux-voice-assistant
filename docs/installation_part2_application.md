# Install Application

You can install the application in different ways. We recommend to use Docker Compose if not the prebuilt image. But if you dont want to use Docker you can also install it directly on your system.

## A) Docker Compose (recommended):

<details>
<summary><b>Show Docker Compose installation</b></summary>

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
cp .env.example .env
```

💡 **Note:** The LVA_VERSION variable downloads the latest available version of LVA. If you want another version, you can specify it with the LVA_VERSION variable.

You can change the used version in the docker-compose.yml.

### Docker Image Tags

| Tag | Description | Example |
|-----|-------------|---------|
| `latest` | Latest stable release | `ghcr.io/ohf-voice/linux-voice-assistant:latest` |
| `nightly` | Latest development build | `ghcr.io/ohf-voice/linux-voice-assistant:nightly` |
| `x.y.z` | Specific version release | `ghcr.io/ohf-voice/linux-voice-assistant:1.0.0` |
| `x.y` | Major.Minor with auto updates | `ghcr.io/ohf-voice/linux-voice-assistant:1.0` |
| `<branch>` | Branch-specific build | `ghcr.io/ohf-voice/linux-voice-assistant:my-branch` |

All tags are multi-architecture images for `linux/amd64`, `linux/aarch64` and `linux/arm/v7` (32-bit ARM, e.g. Raspberry Pi 2/3 with a 32-bit OS, Orange Pi, Allwinner H3). Docker selects the matching image for your platform automatically.

Edit the .env file and change the values to your needs:
``` sh
vim .env
```

You can change various settings, for example the audio sounds which are played when the wake word is detected or when the timer is finished. You also need to set the correct user and group id, and the pulseaudio server in the `.env`file.

💡 **Note:** You can exit vim with `:wq` or `:q!` if you dont want to save the changes.

Start the application:
``` sh
docker compose up -d
```

💡 **Note:** If you want to use the application with a different user, you need to change the user in the .env file. Dont forget to change the UID from the user. The docker container will run until you stop it. It will restart automatically after a reboot.

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

💡 **Music Assistant Local Audio player:** LVA can duck and control a Sendspin player such as the Local Audio add-on that runs next to it. [docs/docker](docker/README.md) has a Compose project for the player and explains how the two are connected.

</details>

## B) Bare Metal:

<details>
<summary><b>Show bare metal installation</b></summary>

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

💡 **Note:** For hardware with limited performance (like Raspberry Pi Zero 2W or other SBCs), you may need to adjust compilation parameters to ensure the build process completes successfully. The script supports custom `CXXFLAGS` and `MAKEFLAGS`:

- `--cxxflags`: Custom C++ compilation flags (default: `-O1 -g0`)
- `--makeflags`: Custom make flags (default: `-j1`)

For example, to optimize for low-performance hardware:
``` sh
script/setup --cxxflags="-O1 -g0" --makeflags="-j1"
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
Environment=PULSE_SERVER="/run/user/<replace_with_your_user_id>/pulse/native"
Environment=XDG_RUNTIME_DIR="/run/user/<replace_with_your_user_id>"
Environment=PULSE_COOKIE="/home/pi/linux-voice-assistant/tmp_pulse_cookie"
Environment=PREFERENCES_FILE="/home/pi/linux-voice-assistant/preferences.json"
# Environment=NETWORK_INTERFACE="eth0"
# Environment=MAC_ADDRESS="02:00:00:00:00:01"
# Environment=HOST="0.0.0.0"
# Environment=PORT="6053"
# Environment=ENABLE_ENCRYPTION="1"
# Environment=ENCRYPTION_KEY_FILE="/home/pi/linux-voice-assistant/encryption.key"
# Environment=AUDIO_INPUT_DEVICE="default"
# Environment=AUDIO_INPUT_BLOCK_SIZE="1024"
# Environment=AUDIO_OUTPUT_DEVICE="pulse"
# Environment=MUSIC_OUTPUT_DEVICE="pulse"
# Environment=MPV_PLAYER_DUCK_MODE="duck"
# Environment=ENABLE_SENDSPIN_BRIDGE="1"
# Environment=SENDSPIN_CONTROL_DIR="/run/sendspin-cli"
# Environment=SENDSPIN_PLAYER_DUCK_MODE="pulse"
# Environment=MIC_VOLUME="100"
# Environment=MIC_AUTO_GAIN="0"
# Environment=MIC_NOISE_SUPPRESSION="0"
# Environment=AUDIO_INPUT_CHANNELS=1
# Environment=ENABLE_THINKING_SOUND="1"
# Environment=WAKE_WORD_ROOT_DIR="/home/pi/linux-voice-assistant/wakewords"
# Environment=WAKE_MODEL="okay_nabu"
# Environment=WAKE_MODEL_2="hey_jarvis"
# Environment=STOP_MODEL="stop"
# Environment=TIMER_MAX_RING_SECONDS="900"
# Environment=REFACTORY_SECONDS="0"
# Environment=CONTINUE_CONVERSATION_DELAY="0.5"
# Environment=WAKEUP_SOUND="sounds/wake_word_triggered.flac"
# Environment=START_LISTENING_SOUND="sounds/start_listening_button.flac"
# Environment=TIMER_FINISHED_SOUND="sounds/timer_finished.flac"
# Environment=PROCESSING_SOUND="sounds/processing.wav"
# Environment=LISTEN_DURING_WAKE_SOUND="0"
# Environment=MUTE_SOUND="sounds/mute_switch_on.flac"
# Environment=UNMUTE_SOUND="sounds/mute_switch_off.flac"
# Environment=PIPELINE_ENDED_SOUND="sounds/pipeline_ended_low.flac"
# Environment=BUTTON_DOUBLE_PRESS_SOUND="sounds/button_double_press.flac"
# Environment=BUTTON_TRIPLE_PRESS_SOUND="sounds/button_triple_press.flac"
# Environment=BUTTON_LONG_PRESS_SOUND="sounds/button_long_press.flac"
# Environment=PERIPHERAL_HOST="0.0.0.0"
# Environment=PERIPHERAL_PORT="6055"
# Environment=PERIPHERAL_VOLUME_STEP="%(default)s"
# Environment=PERIPHERAL_STARTUP_WAIT="2.0"
# Environment=ENABLE_REMOTE_UNMUTE="1"
# Environment=DISABLE_PERIPHERAL_API="1"
# Environment=ENABLE_ZEROCONF="1"
# Environment=ENABLE_OUTPUT_ONLY="1"
ExecStart=/home/pi/linux-voice-assistant/docker-entrypoint.sh
# ExecStart=/home/pi/linux-voice-assistant/docker-entrypoint.sh --additional-parameter-if-you-want
Restart=on-failure
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

</details>

## C) Connecting to Home Assistant:

Usually HomeAssistant will discover your device with mDNS. If that does not work, this are the manual steps:

1. In Home Assistant, go to "Settings" -> "Device & services"
2. Click the "Add integration" button
3. Choose "ESPHome" and then "Set up another instance of ESPHome"
4. Enter the IP address of your voice satellite with port 6053
5. Click "Submit"
6. If you enabled encryption (see [Feature: API Encryption](features.md#feature-api-encryption)), Home Assistant asks for it now. Enter the key from LVA's key file.
