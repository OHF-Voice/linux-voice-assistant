#!/usr/bin/env python3
"""
ReSpeaker 2-Mic Pi HAT – Linux Voice Assistant peripheral controller.

Hardware layout
---------------
  LED 0  : left  (above MIC_L)  →  APA102 via SPI0
  LED 1  : centre                →  APA102 via SPI0
  LED 2  : right (above MIC_R)  →  APA102 via SPI0
  Button : context action        →  GPIO 17 (active low, internal pull-up)

LED behaviours
--------------
  idle             : all off
  wake_word        : brief blue flash on all 3 LEDs
  listening        : cyan chase (one LED sweeps left → right → left)
  thinking         : yellow pulse on all 3 LEDs
  tts_speaking     : green breathe on all 3 LEDs
  muted            : LED 0 and LED 2 solid red (mic positions), centre off
  error            : red flash on all 3 LEDs
  timer_ringing    : blue flash on all 3 LEDs (repeating)
  timer_ticking    : all 3 dim cyan, brightness proportional to time left
  media_playing    : dim green steady on all 3 LEDs
  not_ready/no_ha  : dim red pulse on all 3 LEDs

Button behaviour (context action — same priority as HA Voice PE centre button)
-------------------------------------------------------------------------------
  timer ringing               → stop_timer_ringing
  wake word / listening /
    thinking (pipeline active) → stop_pipeline
  tts speaking                → stop_speaking
  media playing               → stop_media_player
  idle / anything else        → start_listening

Install dependencies
--------------------
  pip install websockets spidev RPi.GPIO

Enable SPI on the Pi:
  Add  dtparam=spi=on  to /boot/firmware/config.txt and reboot.
  (The seeed-voicecard installer does this automatically.)

Run
---
  python3 respeaker_2mic_hat.py
  python3 respeaker_2mic_hat.py --host 192.168.1.50 --port 6055 --debug
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import math
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
    import spidev  # type: ignore[import]
    _HAS_SPI = True
except ImportError:
    _HAS_SPI = False
    logging.warning("spidev not found – LED output will be simulated")

try:
    import RPi.GPIO as GPIO  # type: ignore[import]
    _HAS_GPIO = True
except ImportError:
    _HAS_GPIO = False
    logging.warning("RPi.GPIO not found – button input disabled")

try:
    import websockets  # type: ignore[import]
except ImportError:
    sys.exit("websockets not installed. Run: pip install websockets")


# ===========================================================================
# Configuration
# ===========================================================================

DEFAULT_LVA_HOST  = "localhost"
DEFAULT_LVA_PORT  = 6055

# APA102 SPI
SPI_BUS           = 0
SPI_DEVICE        = 0          # /dev/spidev0.0
SPI_SPEED_HZ      = 8_000_000
LED_COUNT         = 3
LED_BRIGHTNESS    = 0.6        # 0.0–1.0 default brightness

# GPIO
BTN_ACTION        = 17         # The single onboard button
BTN_DEBOUNCE_MS   = 150

RECONNECT_DELAY_S = 3.0


# ===========================================================================
# Logging
# ===========================================================================

_LOGGER = logging.getLogger("respeaker2mic")


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
# Shared state
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
# APA102 driver
# ===========================================================================

RGB = Tuple[int, int, int]

OFF    : RGB = (0,   0,   0)
RED    : RGB = (255, 0,   0)
BLUE   : RGB = (0,   0,   255)
CYAN   : RGB = (0,   200, 200)
GREEN  : RGB = (0,   200, 50)
YELLOW : RGB = (220, 180, 0)
DIM_RED: RGB = (80,  0,   0)


def _scale(color: RGB, factor: float) -> RGB:
    f = max(0.0, min(1.0, factor))
    return (int(color[0] * f), int(color[1] * f), int(color[2] * f))


class APA102:
    """
    Minimal APA102 LED strip driver over SPI.

    Frame format (per LED): 0xE0|brightness5, blue, green, red
    """

    def __init__(self) -> None:
        self._pixels: list[RGB] = [OFF] * LED_COUNT
        self._spi = None

        if _HAS_SPI:
            try:
                self._spi = spidev.SpiDev()
                self._spi.open(SPI_BUS, SPI_DEVICE)
                self._spi.max_speed_hz = SPI_SPEED_HZ
                self._spi.mode = 0b01  # APA102 uses SPI mode 1
                _LOGGER.info(
                    "APA102 SPI driver opened (/dev/spidev%d.%d)",
                    SPI_BUS, SPI_DEVICE,
                )
            except Exception as exc:
                _LOGGER.error("Failed to open SPI: %s – LEDs simulated", exc)
                self._spi = None

    def set(self, index: int, color: RGB) -> None:
        self._pixels[index % LED_COUNT] = color

    def set_all(self, color: RGB) -> None:
        self._pixels = [color] * LED_COUNT

    def show(self, brightness: float = LED_BRIGHTNESS) -> None:
        bright5 = max(0, min(31, int(brightness * 31)))

        frame: list[int] = [0x00, 0x00, 0x00, 0x00]  # start frame
        for r, g, b in self._pixels:
            br = max(0, min(255, int(r * brightness)))
            bg = max(0, min(255, int(g * brightness)))
            bb = max(0, min(255, int(b * brightness)))
            frame += [0xE0 | bright5, bb, bg, br]
        # end frame: ⌈n/2⌉ bytes of 0xFF
        frame += [0xFF] * math.ceil(LED_COUNT / 2)

        if self._spi is not None:
            try:
                self._spi.xfer2(frame)
            except Exception as exc:
                _LOGGER.debug("SPI write error: %s", exc)
        else:
            pixels = ", ".join(f"rgb{p}" for p in self._pixels)
            _LOGGER.debug("LEDs [simulated]: %s  brightness=%.2f", pixels, brightness)

    def off(self) -> None:
        self._pixels = [OFF] * LED_COUNT
        self.show(0)

    def close(self) -> None:
        self.off()
        if self._spi is not None:
            self._spi.close()


# ===========================================================================
# LED animator
# ===========================================================================

class LEDAnimator:
    """
    Drives the 3 APA102 LEDs with state-appropriate animations.

    Each animation runs as an asyncio Task. Calling set_state() cancels the
    previous task and starts the new one immediately.

    LED positions:
      0 = left  (above MIC_L)
      1 = centre
      2 = right (above MIC_R)
    """

    def __init__(self, leds: APA102, shared: SharedState) -> None:
        self._leds = leds
        self._shared = shared
        self._task: Optional[asyncio.Task] = None
        self._current_state: AssistState = AssistState.NOT_READY

    def set_state(self, state: AssistState) -> None:
        if self._current_state == state:
            return
        self._current_state = state
        self._cancel()

        _LOGGER.debug("LED state → %s", state.value)

        snap = self._shared.snapshot

        if state == AssistState.IDLE:
            self._task = asyncio.create_task(self._idle())

        elif state == AssistState.NOT_READY:
            self._task = asyncio.create_task(self._pulse_all(DIM_RED))

        elif state == AssistState.WAKE_WORD:
            self._task = asyncio.create_task(
                self._flash_all(BLUE, flashes=2, on_ms=120, off_ms=80)
            )

        elif state == AssistState.LISTENING:
            self._task = asyncio.create_task(self._chase(CYAN))

        elif state == AssistState.THINKING:
            self._task = asyncio.create_task(self._pulse_all(YELLOW))

        elif state == AssistState.SPEAKING:
            self._task = asyncio.create_task(self._breathe_all(GREEN))

        elif state == AssistState.MUTED:
            # Left and right LEDs red (mic positions), centre off
            self._task = asyncio.create_task(self._muted())

        elif state == AssistState.ERROR:
            self._task = asyncio.create_task(
                self._flash_all(RED, flashes=3, on_ms=150, off_ms=100, then_off=True)
            )

        elif state == AssistState.TIMER_RINGING:
            # Blue flash on all 3 LEDs, repeating until dismissed
            self._task = asyncio.create_task(
                self._flash_all(BLUE, flashes=0, on_ms=350, off_ms=250, repeat=True)
            )

        elif state == AssistState.TIMER_TICKING:
            self._task = asyncio.create_task(self._timer_tick())

        elif state == AssistState.MEDIA_PLAYING:
            self._task = asyncio.create_task(
                self._steady_all(GREEN, brightness=0.15)
            )

        elif state == AssistState.TTS_FINISHED:
            self._task = asyncio.create_task(self._idle())

        else:
            self._task = asyncio.create_task(self._idle())

    def _cancel(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()

    def cleanup(self) -> None:
        self._cancel()
        self._leds.off()

    # ------------------------------------------------------------------
    # Animation implementations
    # ------------------------------------------------------------------

    async def _idle(self) -> None:
        self._leds.off()

    async def _steady_all(self, color: RGB, brightness: float = LED_BRIGHTNESS) -> None:
        self._leds.set_all(color)
        self._leds.show(brightness)

    async def _muted(self) -> None:
        """Left (MIC_L) and right (MIC_R) LEDs red; centre off."""
        self._leds.set(0, RED)
        self._leds.set(1, OFF)
        self._leds.set(2, RED)
        self._leds.show(0.6)

    async def _flash_all(
        self,
        color: RGB,
        flashes: int = 2,
        on_ms: int = 150,
        off_ms: int = 100,
        then_off: bool = False,
        repeat: bool = False,
    ) -> None:
        """
        Flash all 3 LEDs.

        flashes=0 with repeat=True → repeats until cancelled (timer ringing).
        then_off=True → turn off after the last flash cycle.
        """
        count = 0
        while True:
            self._leds.set_all(color)
            self._leds.show(1.0)
            await asyncio.sleep(on_ms / 1000)
            self._leds.off()
            await asyncio.sleep(off_ms / 1000)
            count += 1
            if not repeat and count >= flashes:
                break
        if then_off:
            self._leds.off()

    async def _chase(self, color: RGB, step_s: float = 0.12) -> None:
        """
        One lit LED bounces left → right → left.
        Gives the impression of listening / scanning.
        """
        sequence = [0, 1, 2, 1]  # left, centre, right, centre, repeat
        pos = 0
        while True:
            for i in range(LED_COUNT):
                self._leds.set(i, OFF)
            self._leds.set(sequence[pos % len(sequence)], color)
            self._leds.show(1.0)
            pos += 1
            await asyncio.sleep(step_s)

    async def _pulse_all(self, color: RGB, period: float = 1.0) -> None:
        """All 3 LEDs pulse together in brightness."""
        while True:
            t = time.monotonic()
            brightness = 0.2 + 0.8 * (0.5 + 0.5 * math.sin(2 * math.pi * t / period))
            self._leds.set_all(color)
            self._leds.show(brightness)
            await asyncio.sleep(0.03)

    async def _breathe_all(self, color: RGB, period: float = 2.0) -> None:
        """Slow sine-wave breathe on all 3 LEDs."""
        while True:
            t = time.monotonic()
            brightness = 0.1 + 0.9 * (0.5 + 0.5 * math.sin(2 * math.pi * t / period))
            self._leds.set_all(color)
            self._leds.show(brightness)
            await asyncio.sleep(0.03)

    async def _timer_tick(self) -> None:
        """
        All 3 LEDs dim cyan, brightness proportional to time remaining.
        Full brightness = full time left; almost off = nearly expired.
        """
        while True:
            snap = self._shared.snapshot
            total = max(snap["timer_total_seconds"], 1)
            left  = snap["timer_seconds_left"]
            brightness = max(0.05, min(1.0, left / total))
            self._leds.set_all(CYAN)
            self._leds.show(brightness)
            await asyncio.sleep(0.5)


# ===========================================================================
# Button handler
# ===========================================================================

class ButtonHandler:
    """
    Manages the single onboard button (GPIO 17) using RPi.GPIO.
    Sends context-aware commands to LVA via the asyncio command queue.
    """

    def __init__(
        self,
        state: SharedState,
        loop: asyncio.AbstractEventLoop,
        command_queue: asyncio.Queue,
    ) -> None:
        self._state = state
        self._loop = loop
        self._queue = command_queue

    def setup(self) -> None:
        if not _HAS_GPIO:
            _LOGGER.warning("GPIO unavailable – button not configured")
            return

        GPIO.setmode(GPIO.BCM)
        GPIO.setup(BTN_ACTION, GPIO.IN, pull_up_down=GPIO.PUD_UP)
        GPIO.add_event_detect(
            BTN_ACTION, GPIO.FALLING,
            callback=self._on_press, bouncetime=BTN_DEBOUNCE_MS,
        )
        _LOGGER.info("Button configured (GPIO BCM %d)", BTN_ACTION)

    def cleanup(self) -> None:
        if _HAS_GPIO:
            GPIO.cleanup()

    def _send(self, command: str) -> None:
        _LOGGER.info("Button → %s", command)
        asyncio.run_coroutine_threadsafe(
            self._queue.put(command), self._loop
        )

    def _on_press(self, _channel) -> None:
        """
        Context-sensitive action — mirrors HA Voice PE centre button priority:
          1. Timer ringing              → stop_timer_ringing
          2. Pipeline active            → stop_pipeline
             (wake word / listening / thinking)
          3. TTS speaking               → stop_speaking
          4. Media playing              → stop_media_player
          5. Idle / anything else       → start_listening
        """
        assist = self._state.assist_state

        if assist == AssistState.TIMER_RINGING:
            self._send("stop_timer_ringing")
        elif assist in (AssistState.WAKE_WORD, AssistState.LISTENING, AssistState.THINKING):
            self._send("stop_pipeline")
        elif assist == AssistState.SPEAKING:
            self._send("stop_speaking")
        elif assist == AssistState.MEDIA_PLAYING:
            self._send("stop_media_player")
        else:
            self._send("start_listening")


# ===========================================================================
# WebSocket client
# ===========================================================================

class LVAClient:

    def __init__(
        self,
        host: str,
        port: int,
        state: SharedState,
        animator: LEDAnimator,
        command_queue: asyncio.Queue,
    ) -> None:
        self._uri = f"ws://{host}:{port}"
        self._state = state
        self._animator = animator
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
                self._animator.set_state(AssistState.NOT_READY)
                await asyncio.sleep(RECONNECT_DELAY_S)

    async def _connect(self) -> None:
        _LOGGER.info("Connecting to LVA at %s …", self._uri)
        async with websockets.connect(
            self._uri, ping_interval=20, ping_timeout=10
        ) as ws:
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
            muted = data.get("muted", False)
            self._state.update(
                muted=muted,
                volume=data.get("volume", 1.0),
                ha_connected=data.get("ha_connected", False),
            )
            if muted:
                self._animator.set_state(AssistState.MUTED)
            elif not data.get("ha_connected", False):
                self._animator.set_state(AssistState.NOT_READY)
            else:
                self._animator.set_state(AssistState.IDLE)
            return

        if event == "wake_word_detected":
            self._state.update(assist_state=AssistState.WAKE_WORD)

        elif event == "listening":
            self._state.update(assist_state=AssistState.LISTENING)

        elif event == "thinking":
            self._state.update(assist_state=AssistState.THINKING)

        elif event == "tts_speaking":
            self._state.update(assist_state=AssistState.SPEAKING)

        elif event in ("tts_finished", "idle"):
            # Return to muted indicator if still muted, otherwise idle
            if self._state.muted:
                self._state.update(assist_state=AssistState.MUTED)
            else:
                self._state.update(assist_state=AssistState.IDLE)

        elif event == "muted":
            self._state.update(assist_state=AssistState.MUTED, muted=True)

        elif event == "error":
            reason = data.get("reason", "")
            _LOGGER.warning("LVA error: %s", reason)
            if reason == "ha_disconnected":
                self._state.update(
                    ha_connected=False,
                    assist_state=AssistState.NOT_READY,
                )
            else:
                self._state.update(assist_state=AssistState.ERROR)
                # Auto-recover to idle after showing the error flash
                await asyncio.sleep(2.5)
                if self._state.assist_state == AssistState.ERROR:
                    self._state.update(assist_state=AssistState.IDLE)

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
            muted = data.get("muted", False)
            self._state.update(muted=muted)
            if muted:
                self._state.update(assist_state=AssistState.MUTED)
            elif self._state.assist_state == AssistState.MUTED:
                self._state.update(assist_state=AssistState.IDLE)

        elif event == "zeroconf":
            status = data.get("status", "")
            if status == "connected":
                self._state.update(ha_connected=True)
                _LOGGER.info("Home Assistant connected")
            elif status == "getting_started":
                _LOGGER.info("LVA starting up, waiting for HA …")

        # Sync animator with current state
        self._animator.set_state(self._state.assist_state)


# ===========================================================================
# Main
# ===========================================================================

async def async_main(host: str, port: int) -> None:
    state         = SharedState()
    command_queue: asyncio.Queue = asyncio.Queue()
    loop          = asyncio.get_running_loop()

    leds     = APA102()
    animator = LEDAnimator(leds, state)
    buttons  = ButtonHandler(state, loop, command_queue)
    client   = LVAClient(host, port, state, animator, command_queue)

    # Start hardware
    buttons.setup()

    # Show "not ready" until LVA connects
    animator.set_state(AssistState.NOT_READY)

    def _shutdown(signum, frame) -> None:
        _LOGGER.info("Shutdown requested")
        animator.cleanup()
        buttons.cleanup()
        leds.close()
        sys.exit(0)

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    _LOGGER.info(
        "ReSpeaker 2-Mic controller started – connecting to ws://%s:%d",
        host, port,
    )

    try:
        await client.run_forever()
    finally:
        animator.cleanup()
        buttons.cleanup()
        leds.close()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="ReSpeaker 2-Mic Pi HAT controller for Linux Voice Assistant"
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