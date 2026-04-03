# Satellite 1 HAT Board

Peripheral controller for the [Satellite1 HAT Board](https://futureproofhomes.net/products/satellite1-top-microphone-board) by FutureProofHomes, running alongside the Linux Voice Assistant (LVA) container.

The controller runs as a separate Docker container on the same Raspberry Pi. It connects to LVA's peripheral WebSocket API, drives the 12-LED SK6812 ring with animations that mirror the [Home Assistant Voice PE](https://www.home-assistant.io/voice-pe/) LED behaviour, and maps the four hardware buttons to LVA commands.

---

## Hardware

| Component | Details |
|---|---|
| Microphone array | XMOS XVF3800 (USB, far-field, onboard DSP) |
| LED ring | 12 × SK6812 RGBW NeoPixels |
| Buttons | 4 × tactile (top, bottom, left, right) |
| Audio output | I2S line-out (optional) |
| Interface | Raspberry Pi 40-pin HAT connector |

### Compatible hardware

The Satellite 1 HAT works on any Raspberry Pi with a 40-pin GPIO header. Tested targets:

- Raspberry Pi Zero 2 W
- Raspberry Pi 3 B / B+
- Raspberry Pi 4 B
- Raspberry Pi 5

---

## GPIO pin mapping

| Function | GPIO (BCM) | Notes |
|---|---|---|
| LED ring data | 12 | PWM0, channel 0 |
| Right button (Volume Up) | 17 | Active low, internal pull-up |
| Left button (Volume Down) | 27 | Active low, internal pull-up |
| Top button (Mute / Unmute) | 22 | Active low, internal pull-up |
| Bottom button (Context action) | 23 | Active low, internal pull-up |

---

## Button behaviour

### Right button — Volume Up
Sends `volume_up` to LVA. Each press increases volume by one step.

### Left button — Volume Down
Sends `volume_down` to LVA. Each press decreases volume by one step.

### Top button — Mute / Unmute
Toggles microphone mute. Sends `mute_mic` when unmuted, `unmute_mic` when muted. The LED ring switches to the muted indicator pattern immediately.

### Bottom button — Context action
Sends a command based on the current assistant state, in priority order:

| Current state | Command sent |
|---|---|
| Timer ringing | `stop_timer_ringing` |
| Listening / thinking / wake word active | `stop_listening` |
| TTS speaking | `stop_speaking` |
| Music / media playing | `stop_media_player` |
| Any other (idle) | `start_listening` |

---

## LED ring animations

All animations mirror the Home Assistant Voice PE ESPHome firmware exactly.

| LVA state | Animation | Description |
|---|---|---|
| No HA connection / error | Red twinkle | Random red sparkle across all LEDs |
| Idle | Off | Ring off (or dim if user LED ring is on) |
| Wake word detected | Slow clockwise spin | Two trailing arcs at opposing positions |
| Listening | Fast clockwise spin | Same dual-arc pattern at 50 ms interval |
| Thinking | Pulsing pair | Two opposing LEDs fade in and out |
| TTS speaking | Anticlockwise spin | Dual-arc spin in reverse direction |
| Muted | Solid ring + red indicators | Full ring on; red at positions 3 & 9 (mic locations) |
| Error | Red pulse | All LEDs red, pulsing |
| Timer ticking | Countdown arc | Arc length proportional to `seconds_left / total_seconds` |
| Timer ringing | Pulse + optional red | Full ring pulsing; red at 3 & 9 if muted |

---

## Installation

### Step 1 — Host kernel configuration

Edit `/boot/firmware/config.txt` (or `/boot/config.txt` on older Raspberry Pi OS):

```ini
# Enable PWM on GPIO 12 for the LED ring
dtoverlay=pwm,pin=12,func=4

# Disable onboard audio — it shares PWM0 with GPIO 12
# dtparam=audio=on

# Optional: I2S line-out if using the HAT's audio output
# dtoverlay=hifiberry-dac

# Optional: increase GPU memory on Pi Zero 2 W
# gpu_mem=64
```

Reboot after saving:

```bash
sudo reboot
```

> **Why disable onboard audio?** The Raspberry Pi's 3.5 mm headphone jack uses the same PWM0 hardware peripheral as GPIO 12. They cannot run at the same time. The Satellite 1 HAT's XMOS microphone array connects over USB and is unaffected.

### Step 2 — Add user to GPIO group

```bash
sudo usermod -aG gpio,spi $USER
```

Log out and back in for the group change to take effect.

### Step 3 — File structure

Place all files in the same directory:

```
Satellite1_HAT_Board/
├── Dockerfile
├── compose.yml
├── requirements.txt
└── Satellite1_HAT_Board.py
```

### Step 4 — Build and start

```bash
docker compose up -d
```

Check logs:

```bash
docker compose logs -f
```

---

## Configuration

All configuration is at the top of `Satellite1_HAT_Board.py`:

```python
# LVA connection
DEFAULT_LVA_HOST = "localhost"
DEFAULT_LVA_PORT = 6055

# GPIO pins (BCM numbering)
LED_GPIO_PIN    = 12
BTN_VOLUME_UP   = 17
BTN_VOLUME_DOWN = 27
BTN_MUTE        = 22
BTN_ACTION      = 23

BTN_DEBOUNCE_MS = 150   # Button debounce in milliseconds

# LED ring
LED_COUNT      = 12
LED_BRIGHTNESS = 168    # 0–255, default is 66 % (168)

# Default ring colour  (R, G, B) — matches HA Voice PE default
DEFAULT_R, DEFAULT_G, DEFAULT_B = 24, 187, 242

# Volume step per button press (0.0–1.0)
# Also configurable via --peripheral-volume-step on the LVA side
```

### Command-line arguments

The container command in `docker-compose.yml` accepts:

| Argument | Default | Description |
|---|---|---|
| `--host` | `localhost` | LVA container hostname or IP |
| `--port` | `6055` | LVA peripheral API port |
| `--debug` | off | Enable verbose debug logging |

To connect to LVA running on a different host:

```yaml
command:
  - "--host"
  - "192.168.1.50"
  - "--port"
  - "6055"
```

---

## Connection to LVA

The controller connects to LVA's peripheral WebSocket API at `ws://<host>:6055`. The LVA container must have the peripheral API enabled (it is on by default) and port 6055 must be reachable.

On startup the controller:
1. Connects to LVA and receives a state snapshot (current mute state, volume, HA connection status)
2. Sets the LED ring to the appropriate animation immediately
3. Begins listening for events and button presses

If the connection is lost (LVA restarted, network drop), the controller shows a red twinkle animation and automatically reconnects every 3 seconds.

---

## Drivers summary

| Component | Driver needed | How |
|---|---|---|
| LED ring (SK6812) | PWM kernel overlay | `dtoverlay=pwm,pin=12,func=4` in `config.txt` |
| Buttons | None | `RPi.GPIO` reads `/dev/gpiomem` directly |
| Microphone (XMOS XVF3800) | None | Enumerates as USB audio device automatically |
| I2S line-out (optional) | HiFiBerry DAC overlay | `dtoverlay=hifiberry-dac` in `config.txt` |

---

## Troubleshooting

### LEDs do not light up

1. Confirm `dtoverlay=pwm,pin=12,func=4` is in `config.txt` and the Pi has been rebooted.
2. Confirm onboard audio is disabled (`dtparam=audio=on` is commented out).
3. Check the container is running as root (`user: "0:0"` in compose file) — `rpi-ws281x` requires root for DMA access.
4. Run with `--debug` and look for `LED ring initialised` in the logs. If `rpi_ws281x not found` appears, the Python package failed to install — rebuild the image.

### Buttons do not respond

1. Confirm `/dev/gpiomem` is mapped in the compose `devices` section.
2. Check that the `gpio` group is listed under `group_add`.
3. Run with `--debug` — each button press logs `Button → <command>`.

### Container exits immediately

1. Run `docker compose logs` — a missing `/dev/mem` or `/dev/vcio` device produces a clear error at startup.
2. Ensure `privileged: true` is set in the compose file.

### LVA not reachable

1. Confirm LVA is running and `--disable-peripheral-api` was not passed.
2. With `network_mode: host`, `localhost` resolves to the Pi itself. If LVA is in a separate Docker network (not host mode), use its container IP or service name instead.
3. Check that port 6055 is not blocked by a firewall: `nc -zv localhost 6055`.
