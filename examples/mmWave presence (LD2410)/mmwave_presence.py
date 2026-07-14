#!/usr/bin/env python3
"""mmWave presence peripheral for linux-voice-assistant.

Reads an HLK-LD2410 24 GHz radar on the Raspberry Pi UART and exposes a
presence/occupancy ``binary_sensor`` to Home Assistant through LVA's peripheral
WebSocket API (``register_presence`` + ``set_presence``).

The sensor here streams ASCII ``distance:<cm>`` lines (the firmware shipped on
the FutureProofHomes Satellite1 LD2410). Presence is derived as "a valid
distance seen within ABSENT_TIMEOUT". Adjust ``read_distance`` if your LD2410
speaks the stock binary protocol instead.

Config via environment (all optional):
    LVA_PERIPHERAL_URL          ws://127.0.0.1:6055
    MMWAVE_PORT                 /dev/ttyAMA0
    MMWAVE_BAUD                 115200
    MMWAVE_PRESENCE_MAX_CM      600
    MMWAVE_ABSENT_TIMEOUT       5      (seconds without a hit -> clear)
    MMWAVE_RESEND_INTERVAL      10     (heartbeat re-assert, seconds)
"""

import asyncio
import json
import logging
import os
import re
import time

import serial  # pyserial
import websockets

WS_URL = os.environ.get("LVA_PERIPHERAL_URL", "ws://127.0.0.1:6055")
PORT = os.environ.get("MMWAVE_PORT", "/dev/ttyAMA0")
BAUD = int(os.environ.get("MMWAVE_BAUD", "115200"))
MAX_CM = int(os.environ.get("MMWAVE_PRESENCE_MAX_CM", "600"))
ABSENT_TIMEOUT = float(os.environ.get("MMWAVE_ABSENT_TIMEOUT", "5"))
RESEND_INTERVAL = float(os.environ.get("MMWAVE_RESEND_INTERVAL", "10"))
POLL = 0.1

_DIST_RE = re.compile(rb"distance:(\d+)")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("mmwave-presence")


class Presence:
    """Derives a presence boolean from the distance stream."""

    def __init__(self):
        self.last_seen = 0.0
        self.last_cm = -1

    def update_distance(self, cm, now):
        self.last_cm = cm
        if 0 < cm <= MAX_CM:
            self.last_seen = now

    def detected(self, now):
        return self.last_seen > 0 and (now - self.last_seen) <= ABSENT_TIMEOUT


async def serial_reader(state, loop):
    def _read():
        ser = serial.Serial(PORT, BAUD, timeout=0.2)
        log.info("serial open %s @ %d", PORT, BAUD)
        buf = b""
        while True:
            data = ser.read(64)
            if not data:
                continue
            buf += data
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                m = _DIST_RE.search(line)
                if m:
                    loop.call_soon_threadsafe(state.update_distance, int(m.group(1)), time.monotonic())

    await loop.run_in_executor(None, _read)


async def ws_pusher(state):
    """Register the sensor and push presence; reconnect + re-register on failure."""
    while True:
        try:
            async with websockets.connect(WS_URL) as ws:
                log.info("connected to %s; register_presence", WS_URL)
                await ws.send(json.dumps({"command": "register_presence"}))
                last_sent = None
                last_send_time = 0.0
                while True:
                    now = time.monotonic()
                    det = state.detected(now)
                    # Send on change, and re-assert on a heartbeat so a push that
                    # LVA dropped (e.g. it arrived before HA connected) self-corrects.
                    if det != last_sent or (now - last_send_time) >= RESEND_INTERVAL:
                        await ws.send(json.dumps({"command": "set_presence", "data": {"detected": det}}))
                        if det != last_sent:
                            log.info("presence=%s (last_cm=%s)", det, state.last_cm)
                        last_sent = det
                        last_send_time = now
                    await asyncio.sleep(POLL)
        except Exception as e:  # noqa: BLE001
            log.warning("not ready (%s); retrying in 2s", e)
            await asyncio.sleep(2)


async def main():
    loop = asyncio.get_running_loop()
    state = Presence()
    await asyncio.gather(serial_reader(state, loop), ws_pusher(state))


if __name__ == "__main__":
    asyncio.run(main())
