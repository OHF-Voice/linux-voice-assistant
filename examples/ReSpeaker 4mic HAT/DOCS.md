# ReSpeaker 4-Mic Array HAT

Peripheral controller for the [ReSpeaker 4-Mic Array](https://wiki.seeedstudio.com/ReSpeaker_4_Mic_Array_for_Raspberry_Pi/) by Seeed Studio, running alongside the Linux Voice Assistant (LVA) container.

The controller runs as a separate Docker container on the same Raspberry Pi. It connects to LVA's peripheral WebSocket API and drives the 12-LED APA102 ring with animations that mirror the [Home Assistant Voice PE](https://www.home-assistant.io/voice_control/voice_remote_local_assistant/) LED behaviour.

---

## Hardware

| Component | Details |
|---|---|
| Microphone array | 4 × MEMS mics at board corners (AC108 codec, I2S) |
| LED ring | 12 × APA102 RGB LEDs |
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

### LED ring (APA102)

The APA102 LEDs are driven over SPI. No PWM or DMA is required.

| Signal | GPIO (BCM) | Notes |
|---|---|---|
| MOSI (data) | GPIO 10 | SPI0 MOSI |
| SCLK (clock) | GPIO 11 | SPI0 SCLK |
| CE (bus select) | GPIO 7 (CE1) | Change to GPIO 8 (CE0) if preferred |

---

## LED ring animations

All animations mirror the Home Assistant Voice PE ESPHome firmware exactly.

| LVA state | Animation | Description |
|---|---|---|
| No HA connection / not ready | Red twinkle | Random red sparkle across all LEDs |
| Idle | Off | All LEDs off |
| Wake word detected | Slow clockwise spin | Two trailing arcs at opposing positions |
| Listening | Fast clockwise spin | Same dual-arc pattern at 50 ms interval |
| Thinking | Pulsing pair | Two opposing LEDs fade in and out |
| TTS speaking | Anticlockwise spin | Dual-arc spin in reverse direction |
| Muted | Solid ring + red indicators | Full ring on; red at LEDs 1, 4, 7, 10 (the 4 mic corners) |
| Error | Red pulse | All LEDs red, pulsing |
| Timer ticking | Countdown arc | Arc proportional to `seconds_left / total_seconds` |
| Timer ringing | Pulse + optional red | Full ring pulsing; red at corners if muted |

### Mic indicator positions

The four mics sit at the **corners** of the square board. On the 12-LED ring (30° per step), the corners land at 45°, 135°, 225° and 315°, which corresponds to **LEDs 1, 4, 7, 10**. When muted, these four LEDs turn red so the user can immediately identify the microphone positions.

```
          LED 0
     11 ·     · 1  ← MIC corner (top-right)
   10 ·         · 2
  9 ·             · 3
   8 ·         · 4  ← MIC corner (bottom-right)
     7 ·     · 5
          LED 6
     5 ·     · 7  ← MIC corner (bottom-left)
   4 ·         · 8
  ... (mirrored)
     MIC corner (top-left) → LED 10

  Mic corners: LEDs 1, 4, 7, 10
```

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

After rebooting, verify the microphone appears:

```bash
arecord -l
# Expected output includes:
# card X: seeed4micvoicec [seeed-4mic-voicecard], device 0: ...
```

> **Note:** The installer modifies `/boot/firmware/config.txt` automatically. Check the file after running `install.sh` before proceeding to step 2 — SPI may already be enabled.

### Step 2 — Enable SPI in config.txt

If the seeed-voicecard installer did not already enable SPI, add to `/boot/firmware/config.txt`:

```ini
dtparam=spi=on
```

Reboot and verify:

```bash
ls /dev/spidev*
# Should show: /dev/spidev0.0  /dev/spidev0.1
```

### Step 3 — Add user to GPIO and SPI groups

```bash
sudo usermod -aG gpio,spi $USER
```

Log out and back in. Check your UID:

```bash
id -u $USER
```

If it is not `1000`, update the `user:` field in `docker-compose.yml` to match.

### Step 4 — File structure

```
respeaker_4mic_hat/
├── Dockerfile
├── compose.yml
├── requirements.txt
└── respeaker_4mic_hat.py
```

### Step 5 — Build and start

```bash
cd respeaker4mic
docker compose up -d
```

Check logs:

```bash
docker compose logs -f
```

---

## Configuration

All configuration is at the top of `respeaker_4mic_hat.py`:

```python
# LVA connection
DEFAULT_LVA_HOST = "localhost"
DEFAULT_LVA_PORT = 6055

# APA102 LED ring
LED_COUNT      = 12
SPI_BUS        = 0
SPI_DEVICE     = 1      # 1 = CE1 (/dev/spidev0.1), 0 = CE0 (/dev/spidev0.0)
LED_BRIGHTNESS = 10     # 0–31 (APA102 global brightness register)

# Default ring colour (R, G, B) — matches HA Voice PE default
DEFAULT_R, DEFAULT_G, DEFAULT_B = 24, 187, 242
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
| Microphone array (AC108) | **seeed-voicecard** | `./install.sh` on host, then reboot |
| LED ring (APA102) | SPI overlay | `dtparam=spi=on` in `config.txt` |

---

## Troubleshooting

### Microphone not detected by LVA

1. Confirm `arecord -l` shows `seeed-4mic-voicecard` on the host.
2. If not, re-run `sudo ./install.sh` from the seeed-voicecard repository and reboot.
3. On Raspberry Pi 5 with kernel 6.6+, check the [seeed-voicecard GitHub issues](https://github.com/respeaker/seeed-voicecard/issues) for a compatible branch.

### LEDs do not light up

1. Confirm `dtparam=spi=on` is in `/boot/firmware/config.txt` and the Pi has been rebooted.
2. Check `/dev/spidev0.1` exists: `ls /dev/spidev*`. If only `spidev0.0` exists, change `SPI_DEVICE = 0` in the script and update the device mapping in `docker-compose.yml` to `/dev/spidev0.0`.
3. Confirm the container user is in the `spi` group: `groups $USER`.
4. Run with `--debug` and look for `APA102 LED ring initialised` in the logs.

### SPI and seeed-voicecard conflict

The seeed-voicecard driver uses I2S — it does **not** use SPI — so there is no conflict with the APA102 LED ring. Both can run simultaneously without any special configuration.

### LVA not reachable

1. Confirm LVA is running and port 6055 is open: `nc -zv localhost 6055`.
2. With `network_mode: host`, `localhost` resolves to the Pi itself.
3. If LVA runs in a separate Docker network (not host mode), use its container IP or service name as `--host`.