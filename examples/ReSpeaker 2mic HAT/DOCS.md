# ReSpeaker 2-Mic Pi HAT

Peripheral controller for the [ReSpeaker 2-Mic Pi HAT](https://wiki.seeedstudio.com/ReSpeaker_2_Mics_Pi_HAT/) by Seeed Studio, running alongside the Linux Voice Assistant (LVA) container.

The controller runs as a separate Docker container on the same Raspberry Pi. It connects to LVA's peripheral WebSocket API, drives the 3 APA102 RGB LEDs, and maps the single onboard button to context-aware LVA commands.

---

## Hardware

| Component | Details |
|---|---|
| Microphone array | 2 × MEMS mics — MIC_L (left), MIC_R (right) — WM8960 codec, I2S |
| LED strip | 3 × APA102 RGB LEDs in a row |
| Speaker output | JST 2.0 connector + 3.5 mm audio jack (WM8960 codec) |
| Button | 1 × onboard tactile button (GPIO 17) |
| Interface | Raspberry Pi 40-pin HAT connector |

### Compatible hardware

| Board | Notes |
|---|---|
| Raspberry Pi Zero 2 W | Recommended for compact builds |
| Raspberry Pi 3 B / B+ | Fully supported |
| Raspberry Pi 4 B | Fully supported |
| Raspberry Pi 5 | Requires seeed-voicecard driver compatible with kernel 6.6+ |

---

## GPIO pin mapping

### LED strip (APA102)

| Signal | GPIO (BCM) | Notes |
|---|---|---|
| MOSI (data) | GPIO 10 | SPI0 MOSI |
| SCLK (clock) | GPIO 11 | SPI0 SCLK |
| CE | GPIO 8 (CE0) | /dev/spidev0.0 |

### Button

| Function | GPIO (BCM) | Notes |
|---|---|---|
| Context action | GPIO 17 | Active low, internal pull-up enabled |

---

## LED positions

```
  ┌──────────────────────────────────────┐
  │  [LED 0]    [LED 1]    [LED 2]       │
  │  MIC_L      centre     MIC_R         │
  └──────────────────────────────────────┘
```

LED 0 sits above MIC_L and LED 2 sits above MIC_R. When the microphone is muted, these two LEDs turn red to visually indicate the mic positions are disabled. The centre LED (1) is used for general status animations.

---

## LED animations

| LVA state | Animation | LEDs |
|---|---|---|
| Not ready / no HA connection | Dim red pulse | All 3 |
| Idle | Off | All 3 off |
| Wake word detected | Blue flash (×2) | All 3 |
| Listening | Cyan chase (bouncing left ↔ right) | 1 at a time |
| Thinking | Yellow pulse | All 3 |
| TTS speaking | Green breathe (slow sine) | All 3 |
| **Muted** | **Solid red** | **LEDs 0 & 2 only (mic positions)** |
| Error | Red flash (×3), then off | All 3 |
| **Timer ringing** | **Blue flash (repeating)** | **All 3** |
| Timer ticking | Dim cyan, brightness ∝ time left | All 3 |
| Media playing | Dim green steady | All 3 |

---

## Button behaviour

The single onboard button sends a context-aware command to LVA, mirroring the Home Assistant Voice PE centre button priority:

| Current state | Command sent |
|---|---|
| Timer ringing | `stop_timer_ringing` |
| Wake word / listening / thinking | `stop_pipeline` |
| TTS speaking | `stop_speaking` |
| Music / media playing | `stop_media_player` |
| Any other (idle) | `start_listening` |

---

## Installation

### Step 1 — Install the seeed-voicecard audio driver

Run on the **host Raspberry Pi** (not inside Docker):

```bash
git clone https://github.com/respeaker/seeed-voicecard
cd seeed-voicecard
sudo ./install.sh
sudo reboot
```

After rebooting, verify the microphone and speaker appear:

```bash
arecord -l
# Should list: seeed-2mic-voicecard

aplay -l
# Should list: seeed-2mic-voicecard
```

> **Note:** The seeed-voicecard installer also enables SPI automatically. Check `/boot/firmware/config.txt` after running `install.sh` before proceeding to step 2.

### Step 2 — Enable SPI in config.txt

If `dtparam=spi=on` is not already present in `/boot/firmware/config.txt`, add it:

```ini
dtparam=spi=on

# Also disable onboard audio if you see conflicts:
# dtparam=audio=on    ← comment this out
```

Reboot and verify:

```bash
ls /dev/spidev*
# Should show: /dev/spidev0.0
```

### Step 3 — Add user to GPIO and SPI groups

```bash
sudo usermod -aG gpio,spi $USER
```

Log out and back in. Check your UID:

```bash
id -u $USER
```

If it is not `1000`, update the `user:` field in `compose.yml` to match.

### Step 4 — File structure

```
respeaker_2mic_hat/
├── Dockerfile
├── compose.yml
├── requirements.txt
└── respeaker_2mic_hat.py
```

### Step 5 — Build and start

```bash
docker compose up -d
```

Check logs:

```bash
docker compose logs -f
```

---

## Using the speaker output with LVA

The WM8960 codec on the HAT also drives the speaker. After installing seeed-voicecard, pass the device name to LVA:

```bash
--audio-output-device "seeed-2mic-voicecard"
```

Or set the environment variable in LVA's compose file:

```yaml
environment:
  - AUDIO_OUTPUT_DEVICE=seeed-2mic-voicecard
```

---

## Configuration

All configuration is at the top of `respeaker_2mic_hat.py`:

```python
# LVA connection
DEFAULT_LVA_HOST = "localhost"
DEFAULT_LVA_PORT = 6055

# APA102 SPI
SPI_BUS        = 0
SPI_DEVICE     = 0          # /dev/spidev0.0
SPI_SPEED_HZ   = 8_000_000
LED_COUNT      = 3
LED_BRIGHTNESS = 0.6        # 0.0–1.0

# GPIO button
BTN_ACTION     = 17
BTN_DEBOUNCE_MS = 150
```

### Command-line arguments

| Argument | Default | Description |
|---|---|---|
| `--host` | `localhost` | LVA container hostname or IP |
| `--port` | `6055` | LVA peripheral API port |
| `--debug` | off | Enable verbose debug logging |

---

## Drivers summary

| Component | Driver needed | How |
|---|---|---|
| Microphones + speaker (WM8960) | **seeed-voicecard** | `./install.sh` on host, then reboot |
| LED strip (APA102) | SPI overlay | `dtparam=spi=on` in `config.txt` |
| Button | None | `RPi.GPIO` reads `/dev/gpiomem` directly |

---

## Troubleshooting

### Microphone or speaker not detected by LVA

1. Confirm `arecord -l` and `aplay -l` show `seeed-2mic-voicecard` on the host.
2. If not, re-run `sudo ./install.sh` and reboot.
3. On Raspberry Pi 5 with kernel 6.6+, check the [seeed-voicecard GitHub issues](https://github.com/respeaker/seeed-voicecard/issues) for a compatible branch.

### LEDs do not light up

1. Confirm `dtparam=spi=on` is in `/boot/firmware/config.txt` and the Pi has been rebooted.
2. Check `/dev/spidev0.0` exists: `ls /dev/spidev*`.
3. Confirm the container user is in the `spi` group: `groups $USER`.
4. Run with `--debug` and look for `APA102 SPI driver opened` in the logs. If `spidev not found` appears, the Python package failed to install — rebuild the image.

### Button does not respond

1. Confirm `/dev/gpiomem` is mapped in the compose `devices` section.
2. Confirm the container user is in the `gpio` group.
3. Run with `--debug` — each button press logs `Button → <command>`.

### LVA not reachable

1. Confirm LVA is running and port 6055 is open: `nc -zv localhost 6055`.
2. With `network_mode: host`, `localhost` resolves to the Pi itself.
3. If LVA runs in a separate Docker network, use its container IP or service name as `--host`.