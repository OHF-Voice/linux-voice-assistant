# mmWave Presence (LD2410)

A minimal peripheral controller that turns an **HLK-LD2410 24 GHz mmWave radar**
into a Home Assistant **presence (`binary_sensor`)** on your LVA device, using
the [peripheral WebSocket API](../../docs/peripheral_api.md).

Unlike the LED/button examples, this one has no display — it only *reports* a
sensor reading. It demonstrates the `register_presence` + `set_presence`
commands and is a good template for exposing any other binary sensor.

## Hardware

| LD2410 pin | Raspberry Pi pin | Notes |
|------------|------------------|-------|
| 5V / VCC   | 5V (pin 2/4)     | |
| GND        | GND (pin 6)      | |
| TX         | RXD / GPIO15 (pin 10) | sensor → Pi |
| RX         | TXD / GPIO14 (pin 8)  | Pi → sensor (optional) |

The sensor is read on the Pi's primary UART (`/dev/ttyAMA0`). This example
assumes the FutureProofHomes Satellite1 LD2410 firmware, which streams ASCII
`distance:<cm>` lines at 115200 baud. If your LD2410 uses the stock binary
protocol, adapt the `distance` parsing in `mmwave_presence.py`.

### Enabling the UART

On a Raspberry Pi, free the PL011 UART and route it to the GPIO header by adding
to `/boot/firmware/config.txt`:

```
enable_uart=1
dtoverlay=disable-bt
```

Then stop a login console from grabbing it:

```bash
sudo systemctl mask serial-getty@ttyAMA0.service
# and remove any "console=serial0,115200" from /boot/firmware/cmdline.txt
sudo reboot
```

Confirm the stream:

```bash
sudo cat /dev/ttyAMA0    # should print distance:123 lines
```

## How it works

1. Reads the distance stream in a background thread.
2. Derives presence: occupied while a valid distance (`0 < d ≤ MMWAVE_PRESENCE_MAX_CM`)
   was seen within `MMWAVE_ABSENT_TIMEOUT` seconds.
3. Sends `register_presence` on connect, then `set_presence {"detected": …}` on
   every change (plus a heartbeat re-assert so a push that arrives before Home
   Assistant has connected self-corrects).
4. Reconnects and re-registers automatically if LVA restarts.

Home Assistant shows it as `binary_sensor.<satellite>_presence` (device class
`occupancy`) on the LVA ESPHome device. New entities only appear after the
ESPHome integration is reloaded (register before HA enumerates, or reload once).

## Installation

```bash
docker compose up -d --build
docker compose logs -f
```

Or run it directly:

```bash
pip install -r requirements.txt
python mmwave_presence.py
```

## Configuration

All optional, via environment variables (see `compose.yml`):

| Variable | Default | Description |
|----------|---------|-------------|
| `LVA_PERIPHERAL_URL` | `ws://127.0.0.1:6055` | LVA peripheral API endpoint |
| `MMWAVE_PORT` | `/dev/ttyAMA0` | Serial device |
| `MMWAVE_BAUD` | `115200` | Baud rate |
| `MMWAVE_PRESENCE_MAX_CM` | `600` | Max distance counted as presence |
| `MMWAVE_ABSENT_TIMEOUT` | `5` | Seconds without a hit before clearing |
| `MMWAVE_RESEND_INTERVAL` | `10` | Heartbeat re-assert interval |

## Troubleshooting

- **`not ready (...)` in the logs** — LVA (or its peripheral API on port 6055)
  is not up yet. The script retries automatically.
- **No `distance:` lines** — the UART is not freed (see *Enabling the UART*), or
  TX/RX are swapped, or the baud rate is wrong.
- **Sensor never clears** — mmWave sees through walls and has a wide field of
  view; lower `MMWAVE_PRESENCE_MAX_CM` or raise `MMWAVE_ABSENT_TIMEOUT` to taste.
- **Entity missing in HA** — reload the ESPHome integration so HA re-enumerates.
