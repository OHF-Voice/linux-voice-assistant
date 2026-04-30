#!/usr/bin/env python3
"""
ReSpeaker 4-Mic Array HAT – Linux Voice Assistant peripheral controller.

Mirrors the Home Assistant Voice PE LED ring animations and maps four
external GPIO buttons to LVA peripheral API commands.

Hardware layout (ReSpeaker 4-Mic HAT on Raspberry Pi)
------------------------------------------------------
  LED ring   : 12 × APA102 RGB LEDs  →  SPI0 (MOSI GPIO 10, SCLK GPIO 11)
  Microphones: 4 × MEMS mics         →  I2S (AC108 codec, seeed-voicecard driver)

The ReSpeaker 4-Mic HAT has no onboard buttons. Connect your own momentary
tactile switches between the GPIO pins above and GND.

Install dependencies
---------------------
  pip install websockets apa102-pi gpiozero lgpio

Note: gpiozero with the lgpio backend works on Pi 3/4/5 without RPi.GPIO.
  Requires /dev/gpiochip0 (Pi 1–4) or /dev/gpiochip4 (Pi 5).

Enable SPI on the Pi (required for APA102 LEDs):
  Add  dtparam=spi=on  to /boot/firmware/config.txt and reboot.

Run
---
  python3 respeaker_4mic_hat.py
  python3 respeaker_4mic_hat.py --host 127.0.0.1 --port 6055 --debug
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import math
import random
import signal
import sys
import threading
import time
from enum import Enum
from typing import Optional, Tuple

# ---------------------------------------------------------------------------
# Hardware dependencies — gracefully stubbed when not on real Pi hardware
# ---------------------------------------------------------------------------

try:
    from gpiozero import Button as GPIOButton  # type: ignore[import]
    _HAS_GPIO = True
except ImportError:
    _HAS_GPIO = False
    logging.warning("gpiozero not found – button input disabled")

try:
    from apa102_pi.driver import apa102 as _apa102_driver  # type: ignore[import]
    _HAS_APA102 = True
except ImportError:
    _HAS_APA102 = False
    logging.warning("apa102-pi not found – LED output disabled")

try:
    import websockets  # type: ignore[import]
except ImportError:
    sys.exit("websockets not installed. Run: pip install websockets")


# ===========================================================================
# Configuration – edit these to match your wiring
# ===========================================================================

DEFAULT_LVA_HOST = "localhost"
DEFAULT_LVA_PORT = 6055

BTN_DEBOUNCE_MS = 150  # Button debounce in milliseconds

# APA102 LED ring
LED_COUNT      = 12
# SPI bus and device (SPI0, CE1 — CE0 is also valid if CE1 conflicts)
SPI_BUS        = 0
SPI_DEVICE     = 1
# Global brightness: 0–31. APA102 has a separate 5-bit brightness register.
# 10 ≈ 32 % (gentle default), 31 = maximum.
LED_BRIGHTNESS = 10

# Default ring colour (R, G, B) — matches HA Voice PE default
DEFAULT_R, DEFAULT_G, DEFAULT_B = 24, 187, 242

# Reconnect delay when WebSocket connection to LVA is lost
RECONNECT_DELAY_S = 3.0


# ===========================================================================
# Logging
# ===========================================================================

_LOGGER = logging.getLogger("respeaker4mic")


# ===========================================================================
# Assistant state
# ===========================================================================

class AssistState(str, Enum):
    NOT_READY     = "not_ready"
    IDLE          = "idle"
    WAKE_WORD     = "wake_word_detected"
    LISTENING     = "listening"
    THINKING      = "thinking"
    SPEAKING      = "tts_speaking"
    TTS_FINISHED  = "tts_finished"
    ERROR         = "error"
    MUTED         = "muted"
    TIMER_TICKING = "timer_ticking"
    TIMER_RINGING = "timer_ringing"
    MEDIA_PLAYING = "media_player_playing"


# ---------------------------------------------------------------------------
# Shared state (written by WS thread, read by LED thread + button callbacks)
# ---------------------------------------------------------------------------

class SharedState:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.assist_state: AssistState = AssistState.NOT_READY
        self.ha_connected: bool = False
        self.muted: bool = False
        self.volume: float = 1.0
        self.timer_total_seconds: int = 0
        self.timer_seconds_left: int = 0

    def update(self, **kwargs) -> None:
        with self._lock:
            for key, val in kwargs.items():
                setattr(self, key, val)

    @property
    def snapshot(self) -> dict:
        with self._lock:
            return {
                "assist_state":        self.assist_state,
                "ha_connected":        self.ha_connected,
                "muted":               self.muted,
                "volume":              self.volume,
                "timer_total_seconds": self.timer_total_seconds,
                "timer_seconds_left":  self.timer_seconds_left,
            }


# ===========================================================================
# LED ring controller — APA102 via SPI
#
# Mirrors every animation from the ESPHome home-assistant-voice.yaml exactly.
# Key difference from SK6812: APA102 uses SPI (MOSI + SCLK) instead of PWM.
# ===========================================================================

RGB = Tuple[int, int, int]

BLACK: RGB = (0, 0, 0)
RED:   RGB = (255, 0, 0)


def _scale(color: RGB, factor: float) -> RGB:
    f = max(0.0, min(1.0, factor))
    return (int(color[0] * f), int(color[1] * f), int(color[2] * f))


class LEDRing:
    """
    Manages the 12-LED APA102 ring via the apa102-pi SPI driver.

    All animations run in a dedicated daemon thread. Calling
    ``set_animation()`` switches cleanly to the new pattern.
    """

    ANIM_OFF          = "off"
    ANIM_TWINKLE      = "twinkle"
    ANIM_TWINKLE_BLUE = "twinkle_blue"
    ANIM_WAIT_CMD     = "waiting"
    ANIM_LISTENING    = "listening"
    ANIM_THINKING     = "thinking"
    ANIM_REPLYING     = "replying"
    ANIM_MUTED        = "muted"
    ANIM_ERROR        = "error"
    ANIM_TIMER_TICK   = "timer_tick"
    ANIM_TIMER_RING   = "timer_ring"

    def __init__(self, state: SharedState) -> None:
        self._state = state
        self._pixels: list[RGB] = [BLACK] * LED_COUNT
        self._strip = None
        self._animation = self.ANIM_OFF
        self._anim_lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread = threading.Thread(
            target=self._loop, daemon=True, name="led-ring"
        )

        self._index: int = 0
        self._brightness_step: int = 0
        self._brightness_dec: bool = True
        self._twinkle_state: list[float] = [0.0] * LED_COUNT

        if _HAS_APA102:
            self._strip = _apa102_driver.APA102(
                num_led=LED_COUNT,
                global_brightness=LED_BRIGHTNESS,
                mosi=10,
                sclk=11,
                ce=SPI_DEVICE,
            )
            _LOGGER.info(
                "APA102 LED ring initialised (%d LEDs, SPI0 CE%d)",
                LED_COUNT, SPI_DEVICE,
            )

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        self._thread.join(timeout=2)
        self._all_off()
        if _HAS_APA102 and self._strip is not None:
            self._strip.cleanup()

    def set_animation(self, name: str) -> None:
        with self._anim_lock:
            if self._animation != name:
                _LOGGER.debug("LED animation → %s", name)
                self._animation = name
                self._index = 0
                self._brightness_step = 0
                self._brightness_dec = True
                self._twinkle_state = [random.random() for _ in range(LED_COUNT)]

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _write(self) -> None:
        if not _HAS_APA102 or self._strip is None:
            return
        for i, (r, g, b) in enumerate(self._pixels):
            self._strip.set_pixel(i, r, g, b)
        self._strip.show()

    def _all_off(self) -> None:
        self._pixels = [BLACK] * LED_COUNT
        self._write()

    def _set(self, i: int, color: RGB) -> None:
        self._pixels[i % LED_COUNT] = color

    def _color(self) -> RGB:
        return (DEFAULT_R, DEFAULT_G, DEFAULT_B)

    def _pulse_step(self, steps: int = 10) -> float:
        factor = (steps - self._brightness_step) / steps
        if self._brightness_dec:
            self._brightness_step += 1
        else:
            self._brightness_step -= 1
        if self._brightness_step <= 0 or self._brightness_step >= steps:
            self._brightness_dec = not self._brightness_dec
        return factor

    def _apply_mic_indicators(self) -> None:
        """
        Mark all 4 mic positions red on the already-rendered frame.

        ReSpeaker 4-Mic Array has mics at the four corners of the board.
        On a 12-LED ring (30° per step) the corners land at 45°, 135°,
        225°, 315° → LEDs 1, 4, 7, 10.
        Neighbours are blanked so each indicator reads as a distinct dot.
        """
        self._set(0,  BLACK); self._set(1, RED);  self._set(2,  BLACK)
        self._set(3,  BLACK); self._set(4, RED);  self._set(5,  BLACK)
        self._set(6,  BLACK); self._set(7, RED);  self._set(8,  BLACK)
        self._set(9,  BLACK); self._set(10, RED); self._set(11, BLACK)

    def _apply_mic_indicators_pulsed(self, factor: float) -> None:
        """Pulsed version for timer_ring — keeps indicators in sync with ring brightness."""
        self._set(1,  _scale(RED, factor))
        self._set(4,  _scale(RED, factor))
        self._set(7,  _scale(RED, factor))
        self._set(10, _scale(RED, factor))

    # ------------------------------------------------------------------
    # Animation implementations
    # ------------------------------------------------------------------

    def _anim_off(self) -> float:
        self._all_off()
        return 0.1

    def _anim_twinkle(self, color: RGB) -> float:
        FADE = 0.85
        SPARK_PROB = 0.15
        for i in range(LED_COUNT):
            if random.random() < SPARK_PROB:
                self._twinkle_state[i] = 1.0
            else:
                self._twinkle_state[i] *= FADE
            self._set(i, _scale(color, self._twinkle_state[i]))
        self._write()
        return 0.05

    def _anim_spin(self, color: RGB, interval: float, reverse: bool = False) -> float:
        """
        Shared spin for Waiting / Listening / Replying.
        Mirrors ESPHome "Waiting for Command", "Listening For Command",
        and "Replying" effects.
        """
        if reverse:
            self._index = (LED_COUNT + self._index - 1) % LED_COUNT
            offsets = [(0, 1.0), (1, 192/255), (2, 128/255),
                       (6, 1.0), (7, 192/255), (8, 128/255)]
        else:
            offsets = [(0, 1.0), (11, 192/255), (10, 128/255),
                       (6, 1.0), (5,  192/255), (4,  128/255)]

        for i in range(LED_COUNT):
            self._set(i, BLACK)
        for offset, brightness in offsets:
            self._set((self._index + offset) % LED_COUNT, _scale(color, brightness))
        if not reverse:
            self._index = (self._index + 1) % LED_COUNT

        self._write()
        return interval

    def _anim_thinking(self, color: RGB) -> float:
        """Two opposing LEDs pulsing. Mirrors ESPHome "Thinking" effect."""
        factor = self._pulse_step(10)
        for i in range(LED_COUNT):
            if i == self._index % LED_COUNT or i == (self._index + 6) % LED_COUNT:
                self._set(i, _scale(color, factor))
            else:
                self._set(i, BLACK)
        self._write()
        return 0.01

    def _anim_muted(self, color: RGB) -> float:
        """
        Solid ring with red at all 4 mic positions.
        ReSpeaker mics are at the board corners → LEDs 1, 4, 7, 10
        (45°, 135°, 225°, 315° on the 12-LED ring).
        Mirrors ESPHome "Muted or Silent" effect.
        """
        for i in range(LED_COUNT):
            self._set(i, color)
        self._apply_mic_indicators()
        self._write()
        return 0.016

    def _anim_error(self) -> float:
        """All LEDs red, pulsing. Mirrors ESPHome "Error" effect."""
        factor = self._pulse_step(10)
        for i in range(LED_COUNT):
            self._set(i, _scale(RED, factor))
        self._write()
        return 0.01

    def _anim_timer_ring(self, color: RGB, muted: bool) -> float:
        """
        Full ring pulsing; red at all 4 mic positions if muted.
        Mirrors ESPHome "Timer Ring" effect.
        """
        factor = self._pulse_step(10)
        for i in range(LED_COUNT):
            self._set(i, _scale(color, factor))
        if muted:
            self._apply_mic_indicators_pulsed(factor)
        self._write()
        return 0.01

    def _anim_timer_tick(
        self, color: RGB, muted: bool,
        seconds_left: int, total_seconds: int,
    ) -> float:
        """
        Arc proportional to remaining time with sweep marker dip.
        Mirrors ESPHome "Timer Tick" effect exactly.
        """
        total = max(total_seconds, 1)
        timer_ratio = LED_COUNT * seconds_left / total
        last_led_on = max(0, math.ceil(timer_ratio) - 1)

        for i in range(LED_COUNT):
            brightness_dip = (
                0.9 if (i == self._index % LED_COUNT and i != last_led_on) else 1.0
            )
            if i <= timer_ratio:
                brightness = min(brightness_dip * (timer_ratio - i), brightness_dip)
                self._set(i, _scale(color, brightness))
            else:
                self._set(i, BLACK)

        if muted:
            self._apply_mic_indicators()

        self._index = (LED_COUNT + self._index - 1) % LED_COUNT
        self._write()
        return 0.1

    # ------------------------------------------------------------------
    # Animation loop
    # ------------------------------------------------------------------

    def _loop(self) -> None:
        while not self._stop_event.is_set():
            with self._anim_lock:
                anim = self._animation

            snap    = self._state.snapshot
            color   = self._color()
            muted   = snap["muted"]
            t_total = snap["timer_total_seconds"]
            t_left  = snap["timer_seconds_left"]

            if anim == self.ANIM_OFF:
                sleep = self._anim_off()
            elif anim == self.ANIM_TWINKLE:
                sleep = self._anim_twinkle(RED)
            elif anim == self.ANIM_TWINKLE_BLUE:
                sleep = self._anim_twinkle(color)
            elif anim == self.ANIM_WAIT_CMD:
                sleep = self._anim_spin(color, interval=0.1, reverse=False)
            elif anim == self.ANIM_LISTENING:
                sleep = self._anim_spin(color, interval=0.05, reverse=False)
            elif anim == self.ANIM_THINKING:
                sleep = self._anim_thinking(color)
            elif anim == self.ANIM_REPLYING:
                sleep = self._anim_spin(color, interval=0.05, reverse=True)
            elif anim == self.ANIM_MUTED:
                sleep = self._anim_muted(color)
            elif anim == self.ANIM_ERROR:
                sleep = self._anim_error()
            elif anim == self.ANIM_TIMER_RING:
                sleep = self._anim_timer_ring(color, muted)
            elif anim == self.ANIM_TIMER_TICK:
                sleep = self._anim_timer_tick(color, muted, t_left, t_total)
            else:
                sleep = self._anim_off()

            time.sleep(sleep)


# ===========================================================================
# State → animation mapping
# ===========================================================================

def state_to_animation(
    state: AssistState, ha_connected: bool, muted: bool
) -> str:
    if not ha_connected:
        return LEDRing.ANIM_TWINKLE

    if state == AssistState.NOT_READY:
        return LEDRing.ANIM_TWINKLE

    if state == AssistState.TIMER_RINGING:
        return LEDRing.ANIM_TIMER_RING

    if state == AssistState.WAKE_WORD:
        return LEDRing.ANIM_WAIT_CMD

    if state == AssistState.LISTENING:
        return LEDRing.ANIM_LISTENING

    if state == AssistState.THINKING:
        return LEDRing.ANIM_THINKING

    if state == AssistState.SPEAKING:
        return LEDRing.ANIM_REPLYING

    if state == AssistState.ERROR:
        return LEDRing.ANIM_ERROR

    if state == AssistState.MUTED or muted:
        return LEDRing.ANIM_MUTED

    if state == AssistState.TIMER_TICKING:
        return LEDRing.ANIM_TIMER_TICK

    return LEDRing.ANIM_OFF


# ===========================================================================
# WebSocket client
# ===========================================================================

class LVAClient:

    def __init__(
        self,
        host: str,
        port: int,
        state: SharedState,
        leds: LEDRing,
        command_queue: asyncio.Queue,
    ) -> None:
        self._uri = f"ws://{host}:{port}"
        self._state = state
        self._leds = leds
        self._queue = command_queue

    async def run_forever(self) -> None:
        while True:
            try:
                await self._connect()
            except Exception as exc:  # pylint: disable=broad-except
                _LOGGER.warning(
                    "LVA connection lost (%s) – retrying in %.0fs",
                    exc, RECONNECT_DELAY_S,
                )
                self._state.update(
                    ha_connected=False,
                    assist_state=AssistState.NOT_READY,
                )
                self._leds.set_animation(LEDRing.ANIM_TWINKLE)
                await asyncio.sleep(RECONNECT_DELAY_S)

    async def _connect(self) -> None:
        _LOGGER.info("Connecting to LVA at %s …", self._uri)
        async with websockets.connect(self._uri) as ws:
            _LOGGER.info("Connected to LVA peripheral API")
            recv_task = asyncio.create_task(self._recv_loop(ws))
            send_task = asyncio.create_task(self._send_loop(ws))
            done, pending = await asyncio.wait(
                [recv_task, send_task],
                return_when=asyncio.FIRST_EXCEPTION,
            )
            for task in pending:
                task.cancel()
            for task in done:
                if task.exception():
                    raise task.exception()

    async def _recv_loop(self, ws) -> None:
        async for raw in ws:
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue
            await self._handle_event(msg)

    async def _send_loop(self, ws) -> None:
        while True:
            command = await self._queue.get()
            try:
                await ws.send(json.dumps({"command": command}))
            except Exception as exc:  # pylint: disable=broad-except
                _LOGGER.warning("Failed to send command %s: %s", command, exc)
                self._queue.put_nowait(command)
                raise

    async def _handle_event(self, msg: dict) -> None:
        event = msg.get("event", "")
        data  = msg.get("data") or {}

        _LOGGER.debug("Event: %s  data=%s", event, data)

        if event == "snapshot":
            self._state.update(
                muted=data.get("muted", False),
                volume=data.get("volume", 1.0),
                ha_connected=data.get("ha_connected", False),
            )

        elif event == "wake_word_detected":
            self._state.update(assist_state=AssistState.WAKE_WORD)

        elif event == "listening":
            self._state.update(assist_state=AssistState.LISTENING)

        elif event == "thinking":
            self._state.update(assist_state=AssistState.THINKING)

        elif event == "tts_speaking":
            self._state.update(assist_state=AssistState.SPEAKING)

        elif event in ("tts_finished", "idle"):
            self._state.update(assist_state=AssistState.IDLE)

        elif event == "muted":
            self._state.update(assist_state=AssistState.MUTED, muted=True)

        elif event == "pipeline_error":
            _LOGGER.warning("LVA pipeline error: %s", data.get("reason", ""))
            self._state.update(assist_state=AssistState.ERROR)

        elif event == "disconnected":
            _LOGGER.warning("Home Assistant disconnected")
            self._state.update(
                ha_connected=False,
                assist_state=AssistState.NOT_READY,
            )

        elif event == "timer_ticking":
            self._state.update(
                assist_state=AssistState.TIMER_TICKING,
                timer_total_seconds=data.get("total_seconds", 0),
                timer_seconds_left=data.get("seconds_left", 0),
            )

        elif event == "timer_updated":
            self._state.update(
                timer_total_seconds=data.get("total_seconds", 0),
                timer_seconds_left=data.get("seconds_left", 0),
            )

        elif event == "timer_ringing":
            self._state.update(
                assist_state=AssistState.TIMER_RINGING,
                timer_total_seconds=data.get("total_seconds", 0),
                timer_seconds_left=data.get("seconds_left", 0),
            )

        elif event == "media_player_playing":
            self._state.update(assist_state=AssistState.MEDIA_PLAYING)

        elif event == "volume_changed":
            self._state.update(volume=data.get("volume", 1.0))

        elif event == "volume_muted":
            self._state.update(muted=data.get("muted", False))

        elif event == "zeroconf":
            status = data.get("status", "")
            if status == "connected":
                self._state.update(ha_connected=True)
                _LOGGER.info("Home Assistant connected")
            elif status == "getting_started":
                _LOGGER.info("LVA starting up, waiting for HA …")

        # Recompute LED animation after every event
        snap = self._state.snapshot
        anim = state_to_animation(
            snap["assist_state"], snap["ha_connected"], snap["muted"]
        )
        self._leds.set_animation(anim)


# ===========================================================================
# Main
# ===========================================================================

async def async_main(host: str, port: int) -> None:
    state         = SharedState()
    command_queue: asyncio.Queue = asyncio.Queue()
    loop          = asyncio.get_running_loop()

    leds    = LEDRing(state)
    client  = LVAClient(host, port, state, leds, command_queue)

    leds.start()
    leds.set_animation(LEDRing.ANIM_TWINKLE)

    def _shutdown(signum, frame) -> None:
        _LOGGER.info("Shutdown requested")
        leds.stop()
        sys.exit(0)

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    _LOGGER.info(
        "ReSpeaker 4-Mic controller started – connecting to ws://%s:%d",
        host, port,
    )

    try:
        await client.run_forever()
    finally:
        leds.stop()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="ReSpeaker 4-Mic Array HAT controller for Linux Voice Assistant"
    )
    parser.add_argument(
        "--host", default=DEFAULT_LVA_HOST,
        help=f"LVA container hostname or IP (default: {DEFAULT_LVA_HOST})",
    )
    parser.add_argument(
        "--port", type=int, default=DEFAULT_LVA_PORT,
        help=f"LVA peripheral API port (default: {DEFAULT_LVA_PORT})",
    )
    parser.add_argument(
        "--debug", action="store_true",
        help="Enable DEBUG logging",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.INFO,
        format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
    )

    asyncio.run(async_main(args.host, args.port))


if __name__ == "__main__":
    main()