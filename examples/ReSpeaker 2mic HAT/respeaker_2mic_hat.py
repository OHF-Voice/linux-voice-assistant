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
  idle             : solid user color when the LED light is on, else dark
  wake_word        : brief flash on all 3 LEDs (user color from HA Light entity)
  listening        : chase across the LEDs (user color from HA Light entity)
  thinking         : yellow pulse on all 3 LEDs
  tts_speaking     : green breathe on all 3 LEDs
  muted            : LED 0 and LED 2 solid red (mic positions), centre off
  pipeline_error   : red flash on all 3 LEDs
  timer_ringing    : blue flash on all 3 LEDs (repeating)
  timer_ticking    : all 3 dim cyan, brightness proportional to time left
  media_playing    : dim green steady on all 3 LEDs
  not_ready/no_ha  : dim red pulse on all 3 LEDs

On connect the script registers an HA Light entity with LVA via the
register_light command, exposing a single "Voice Assistant" effect to
match the HA Voice PE. Like the Voice PE LED Ring, the light defaults
off: while idle the LEDs hold the user color when it is on and stay
dark when it is off. The pipeline animations always run regardless,
tinted by the user color, so turning the light off only removes the
idle glow; brightness scales every animation.

Button behaviour (context action — same priority as HA Voice PE centre button)
-------------------------------------------------------------------------------
  Single press:
    timer ringing               → stop_timer_ringing
    wake word / listening /
      thinking (pipeline active) /
        tts speaking                → stop_pipeline
    media playing               → stop_media_player
    idle / anything else        → start_listening

  Multi-press (detected via timing):
    double press (< 250ms between releases)  → button_double_press
    triple press (< 250ms between releases)  → button_triple_press
    long press (held > 1000ms)               → button_long_press

Install dependencies
--------------------
  pip install websockets spidev gpiozero lgpio

Enable SPI on the Pi:
  Add  dtparam=spi=on  to /boot/firmware/config.txt and reboot.
  (The seeed-voicecard installer does this automatically.)

Note: gpiozero with the lgpio backend works on Pi 3/4/5.
  Requires /dev/gpiochip0 (Pi 1–4) or /dev/gpiochip4 (Pi 5)
  to be accessible inside the container.

Run
---
  python3 respeaker_2mic_hat.py
  python3 respeaker_2mic_hat.py --host 127.0.0.1 --port 6055 --debug
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
from typing import Callable, Optional, Tuple, Union

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
    from gpiozero import Button  # type: ignore[import]
    _HAS_GPIO = True
except ImportError:
    _HAS_GPIO = False
    logging.warning("gpiozero not found – button input disabled")

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
BTN_DEBOUNCE_MS   = 30         # Mechanical bounce; well inside the 250 ms gesture window.

# Button gesture timing in milliseconds, matching HAVPE's on_multi_click.
MULTIPRESS_TIMEOUT_MS = 250
LONG_PRESS_MS         = 1000

RECONNECT_DELAY_S = 3.0

# HA Light entity registered with LVA via register_light. The same object_id
# is used to route incoming light_command events back to this script.
LIGHT_OBJECT_ID = "leds"
LIGHT_NAME      = "LEDs"
LIGHT_ICON      = "mdi:circle-outline"


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


# Effect name. Must match the LEDLightEntity effects list the peripheral
# registers with LVA via register_light below. Like the HA Voice PE, this
# example exposes only the pipeline animations, which always run and
# cannot be switched off from HA.
EFFECT_VOICE_ASSISTANT = "Voice Assistant"


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
        # Light entity state, driven by HA via light_command events.
        # Defaults match the LEDLightEntity in LVA core so the script
        # behaves sensibly before the first light_command arrives: off by
        # default, like the Voice PE LED Ring, so idle stays dark until the
        # user turns the light on.
        self.light_is_on: bool = False
        self.light_brightness: float = 0.66
        self.light_red: float = 0.094
        self.light_green: float = 0.733
        self.light_blue: float = 0.949
        # Monotonic deadline used by _timer_tick to fade brightness
        # smoothly between sparse timer_updated events.
        self.timer_ends_at: float = 0.0

    def update(self, **kwargs) -> None:
        with self._lock:
            for key, val in kwargs.items():
                setattr(self, key, val)

    def set_timer_progress(self, total_seconds: int, seconds_left: int) -> None:
        """Update timer counters and the monotonic deadline atomically."""
        with self._lock:
            self.timer_total_seconds = max(1, int(total_seconds))
            self.timer_seconds_left = int(seconds_left)
            self.timer_ends_at = time.monotonic() + max(0, int(seconds_left))

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
                "light_is_on":         self.light_is_on,
                "light_brightness":    self.light_brightness,
                "light_red":           self.light_red,
                "light_green":         self.light_green,
                "light_blue":          self.light_blue,
                "timer_ends_at":       self.timer_ends_at,
            }


# ===========================================================================
# APA102 driver
# ===========================================================================

RGB = Tuple[int, int, int]
ColorSource = Union[RGB, Callable[[], RGB]]

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


def _resolve(color: ColorSource) -> RGB:
    """Return an RGB tuple from either a fixed value or a callable.

    Animations that want to follow the user's HA color pass a callable
    so each frame can pick up the latest value. Animations that use a
    fixed semantic color (yellow for thinking, green for speaking, and
    so on) pass the tuple directly.
    """
    return color() if callable(color) else color


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

    def set_state(self, state: AssistState, force: bool = False) -> None:
        # Pass force=True to bypass the no-op guard so a light_command can
        # re-render with the new color or brightness even when the assist
        # state hasn't changed. The light's on/off only gates the idle glow
        # (see _idle); pipeline animations always run, matching the Voice PE.
        if not force and self._current_state == state:
            return
        self._current_state = state
        self._cancel()

        _LOGGER.debug("LED state → %s", state.value)

        if state == AssistState.IDLE:
            self._task = asyncio.create_task(self._idle())

        elif state == AssistState.NOT_READY:
            self._task = asyncio.create_task(self._pulse_all(DIM_RED))

        elif state == AssistState.WAKE_WORD:
            # Flash on all 3 LEDs in the user color (HAVPE style tint).
            self._task = asyncio.create_task(
                self._flash_all(self._user_color, flashes=2, on_ms=120, off_ms=80)
            )

        elif state == AssistState.LISTENING:
            # Chase across the LEDs in the user color (HAVPE style tint).
            self._task = asyncio.create_task(self._chase(self._user_color))

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

    # Helpers that read the HA Light entity state from shared state.

    def _user_color(self) -> RGB:
        snap = self._shared.snapshot
        return (
            int(max(0.0, min(1.0, snap["light_red"])) * 255),
            int(max(0.0, min(1.0, snap["light_green"])) * 255),
            int(max(0.0, min(1.0, snap["light_blue"])) * 255),
        )

    def _brightness(self, base: float = 1.0) -> float:
        """Scale an animation's calculated brightness by user brightness."""
        snap = self._shared.snapshot
        return max(0.0, min(1.0, base * float(snap["light_brightness"])))

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
        """Resting state, matching the HA Voice PE LED Ring.

        Holds the user's color when the HA light is on and stays dark
        when it is off (the light defaults off). Pipeline animations run
        regardless, so turning the light off only removes this idle glow.
        Renders once; set_state(force=True) re-renders on changes.
        """
        if self._shared.snapshot["light_is_on"]:
            self._leds.set_all(self._user_color())
            self._leds.show(self._brightness())
        else:
            self._leds.off()

    async def _steady_all(self, color: ColorSource, brightness: float = LED_BRIGHTNESS) -> None:
        self._leds.set_all(_resolve(color))
        self._leds.show(self._brightness(brightness))

    async def _muted(self) -> None:
        """Left (MIC_L) and right (MIC_R) LEDs red; centre off."""
        self._leds.set(0, RED)
        self._leds.set(1, OFF)
        self._leds.set(2, RED)
        self._leds.show(self._brightness(0.6))

    async def _flash_all(
        self,
        color: ColorSource,
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
            self._leds.set_all(_resolve(color))
            self._leds.show(self._brightness())
            await asyncio.sleep(on_ms / 1000)
            self._leds.off()
            await asyncio.sleep(off_ms / 1000)
            count += 1
            if not repeat and count >= flashes:
                break
        if then_off:
            self._leds.off()

    async def _chase(self, color: ColorSource, step_s: float = 0.12) -> None:
        """
        One lit LED bounces left → right → left.
        Gives the impression of listening / scanning.
        """
        sequence = [0, 1, 2, 1]  # left, centre, right, centre, repeat
        pos = 0
        while True:
            for i in range(LED_COUNT):
                self._leds.set(i, OFF)
            self._leds.set(sequence[pos % len(sequence)], _resolve(color))
            self._leds.show(self._brightness())
            pos += 1
            await asyncio.sleep(step_s)

    async def _pulse_all(self, color: ColorSource, period: float = 1.0) -> None:
        """All 3 LEDs pulse together in brightness."""
        while True:
            t = time.monotonic()
            brightness = 0.2 + 0.8 * (0.5 + 0.5 * math.sin(2 * math.pi * t / period))
            self._leds.set_all(_resolve(color))
            self._leds.show(self._brightness(brightness))
            await asyncio.sleep(0.03)

    async def _breathe_all(self, color: ColorSource, period: float = 2.0) -> None:
        """Slow sine-wave breathe on all 3 LEDs."""
        while True:
            t = time.monotonic()
            brightness = 0.1 + 0.9 * (0.5 + 0.5 * math.sin(2 * math.pi * t / period))
            self._leds.set_all(_resolve(color))
            self._leds.show(self._brightness(brightness))
            await asyncio.sleep(0.03)

    async def _timer_tick(self) -> None:
        """
        All 3 LEDs dim cyan with brightness proportional to time remaining,
        computed from a monotonic deadline so it fades smoothly between
        sparse timer_updated events.
        """
        while True:
            snap = self._shared.snapshot
            total = max(snap["timer_total_seconds"], 1)
            left  = max(0.0, snap["timer_ends_at"] - time.monotonic())
            brightness = max(0.05, min(1.0, left / total))
            self._leds.set_all(CYAN)
            self._leds.show(self._brightness(brightness))
            await asyncio.sleep(0.5)


# ===========================================================================
# Button handler
# ===========================================================================

class ButtonHandler:
    """
    Manages the single onboard button (GPIO 17) using gpiozero.

    gpiozero works with the lgpio backend on Pi 3/4/5 without needing
    RPi.GPIO, which is not supported on Pi 5.
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
        self._button: Optional[object] = None
        self._multipress: Optional["ButtonMultipressHandler"] = None

    def setup(self, multipress: Optional["ButtonMultipressHandler"] = None) -> None:
        if not _HAS_GPIO:
            _LOGGER.warning("GPIO unavailable – button not configured")
            return

        self._multipress = multipress
        if multipress is not None:
            # Let the multipress state machine call the context action
            # when (and only when) a single click resolves.
            multipress.set_single_click_action(self._on_press)
        self._button = Button(BTN_ACTION, pull_up=True, bounce_time=BTN_DEBOUNCE_MS / 1000.0)
        self._button.when_pressed = self._on_button_pressed  # type: ignore[union-attr]
        if multipress is not None:
            self._button.when_released = multipress.on_release  # type: ignore[union-attr]
        _LOGGER.info("Button configured (gpiozero BCM %d)", BTN_ACTION)

    def cleanup(self) -> None:
        if self._button is not None:
            self._button.close()  # type: ignore[union-attr]
            self._button = None

    def _on_button_pressed(self) -> None:
        if self._multipress is not None:
            # The detector dispatches the context action itself once
            # the gesture resolves.
            self._multipress.on_press()
        else:
            # No detector wired: fire immediately on press down.
            self._on_press()

    def _send(self, command: str) -> None:
        _LOGGER.info("Button → %s", command)
        asyncio.run_coroutine_threadsafe(
            self._queue.put(command), self._loop
        )

    def _on_press(self) -> None:
        """Single-click action, mirroring the HA Voice PE centre button.

        Fires once the multipress window resolves with a single press,
        so a double or triple click does not also trigger this. Priority:

          1. Timer ringing              → stop_timer_ringing
          2. Pipeline active            → stop_pipeline
             (wake word / listening / thinking / speaking)
          3. Media playing              → stop_media_player
          4. Idle / anything else       → start_listening
        """
        assist = self._state.assist_state

        if assist == AssistState.TIMER_RINGING:
            self._send("stop_timer_ringing")
        elif assist in (AssistState.WAKE_WORD, AssistState.LISTENING, AssistState.THINKING, AssistState.SPEAKING):
            self._send("stop_pipeline")
        elif assist == AssistState.MEDIA_PLAYING:
            self._send("stop_media_player")
        else:
            self._send("start_listening")


# ===========================================================================
# Button multipress handler
# ===========================================================================

class ButtonMultipressHandler:
    """
    Detects button press patterns: double, triple, and long press.
    
    Timing detection mirrors Home Assistant Voice PE center button:
      - Double press: 2 presses within MULTIPRESS_TIMEOUT_MS
      - Triple press: 3 presses within MULTIPRESS_TIMEOUT_MS
      - Long press: single press held for > LONG_PRESS_MS
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
        self._press_count = 0
        self._last_press_time = 0.0
        self._press_timer: Optional[threading.Timer] = None
        self._lock = threading.Lock()
        self._single_click_action: Optional[Callable[[], None]] = None

    def set_single_click_action(self, action: Callable[[], None]) -> None:
        """Callback fired when the gesture resolves as a single click.
        Invoked from the timer thread."""
        self._single_click_action = action

    def _send(self, command: str) -> None:
        _LOGGER.info("Button → %s", command)
        asyncio.run_coroutine_threadsafe(
            self._queue.put(command), self._loop
        )

    def _on_multipress_timeout(self) -> None:
        """Multipress window expired; dispatch based on the press count."""
        with self._lock:
            count = self._press_count
            self._press_count = 0
            self._press_timer = None

        if count == 1:
            _LOGGER.debug("Single click resolved")
            if self._single_click_action is not None:
                self._single_click_action()
        elif count == 2:
            _LOGGER.debug("Double press detected")
            self._send("button_double_press")
        elif count >= 3:
            _LOGGER.debug("Triple press detected")
            self._send("button_triple_press")

    def _on_long_press(self) -> None:
        """Callback when button held for > LONG_PRESS_MS."""
        with self._lock:
            if self._press_count == 1:
                _LOGGER.debug("Long press detected")
                self._send("button_long_press")
                self._press_count = 0

    def on_press(self) -> None:
        """Called when button is physically pressed."""
        with self._lock:
            current_time = time.time()
            self._press_count += 1
            count = self._press_count

            _LOGGER.debug("Button press #%d", count)

            # Cancel existing timer if any
            if self._press_timer is not None:
                self._press_timer.cancel()
                self._press_timer = None

            # On first press, start long-press timer
            if count == 1:
                self._last_press_time = current_time
                self._press_timer = threading.Timer(
                    LONG_PRESS_MS / 1000.0, self._on_long_press
                )
                self._press_timer.daemon = True
                self._press_timer.start()
            else:
                # On subsequent presses within timeout window, restart multipress timer
                self._press_timer = threading.Timer(
                    MULTIPRESS_TIMEOUT_MS / 1000.0, self._on_multipress_timeout
                )
                self._press_timer.daemon = True
                self._press_timer.start()

    def on_release(self) -> None:
        """Called when button is released. Used to detect long press end."""
        with self._lock:
            if self._press_count == 1 and self._press_timer is not None:
                # Button was released quickly — cancel long-press timer
                # and wait for multipress window
                self._press_timer.cancel()
                self._press_timer = threading.Timer(
                    MULTIPRESS_TIMEOUT_MS / 1000.0, self._on_multipress_timeout
                )
                self._press_timer.daemon = True
                self._press_timer.start()
            # For multi-presses, on_release is not used; timer already running

    def cleanup(self) -> None:
        """Clean up any pending timers."""
        with self._lock:
            if self._press_timer is not None:
                self._press_timer.cancel()
                self._press_timer = None

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
            # Register the LED Light entity with LVA so HA can control it.
            # LVA treats repeat registrations for the same object_id as a
            # no-op, so it's safe to send this on every connect.
            await ws.send(json.dumps({
                "command": "register_light",
                "data": {
                    "name": LIGHT_NAME,
                    "object_id": LIGHT_OBJECT_ID,
                    "icon": LIGHT_ICON,
                    "effects": [EFFECT_VOICE_ASSISTANT],
                    "supports_rgb": True,
                    "supports_brightness": True,
                },
            }))
            # Declare that this peripheral has physical buttons so LVA
            # creates a Button Press event entity in Home Assistant.
            # Idempotent: safe to send on every reconnect.
            await ws.send(json.dumps({"command": "register_button"}))            
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
            # Pick the indicator that should still be visible. A voice
            # initiated timer produces this sequence: timer_ticking
            # (countdown starts) -> tts_speaking (TTS confirms the
            # timer) -> tts_finished (we land here). Going straight
            # to IDLE would hide the countdown for the entire run.
            if self._state.muted:
                self._state.update(assist_state=AssistState.MUTED)
            elif self._state.timer_ends_at > time.monotonic():
                self._state.update(assist_state=AssistState.TIMER_TICKING)
            else:
                self._state.update(assist_state=AssistState.IDLE)

        elif event == "muted":
            # Carries the mic mute state in both directions. Default True so a
            # bare "muted" event (no data) still reads as muted.
            muted = data.get("muted", True)
            self._state.update(muted=muted)
            if muted:
                self._state.update(assist_state=AssistState.MUTED)
            elif self._state.assist_state == AssistState.MUTED:
                self._state.update(assist_state=AssistState.IDLE)

        elif event == "pipeline_error":
            _LOGGER.warning("LVA pipeline error: %s", data.get("reason", ""))
            self._state.update(assist_state=AssistState.ERROR)
            # Auto-recover to idle after showing the error flash
            await asyncio.sleep(2.5)
            if self._state.assist_state == AssistState.ERROR:
                self._state.update(assist_state=AssistState.IDLE)

        elif event == "disconnected":
            _LOGGER.warning("Home Assistant disconnected")
            self._state.update(
                ha_connected=False,
                assist_state=AssistState.NOT_READY,
            )
            self._animator.set_state(AssistState.NOT_READY)

        elif event == "timer_ticking":
            self._state.set_timer_progress(
                data.get("total_seconds", 0),
                data.get("seconds_left", 0),
            )
            self._state.update(assist_state=AssistState.TIMER_TICKING)

        elif event == "timer_updated":
            self._state.set_timer_progress(
                data.get("total_seconds", 0),
                data.get("seconds_left", 0),
            )

        elif event == "timer_ringing":
            self._state.set_timer_progress(
                data.get("total_seconds", 0),
                data.get("seconds_left", 0),
            )
            self._state.update(assist_state=AssistState.TIMER_RINGING)

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
                # If we were sitting in NOT_READY (from an earlier
                # disconnect or from boot), transition back to IDLE now
                # that HA is reachable again. Otherwise keep whatever
                # pipeline state we're already tracking.
                if self._state.assist_state == AssistState.NOT_READY:
                    self._state.update(
                        assist_state=AssistState.MUTED if self._state.muted else AssistState.IDLE,
                    )
                _LOGGER.info("Home Assistant connected")
            elif status == "getting_started":
                _LOGGER.info("LVA starting up, waiting for HA …")

        elif event == "light_command":
            # LVA broadcasts to every connected peripheral; only act on
            # commands targeting our registered Light. The Light exposes a
            # single "Voice Assistant" effect, so there is no effect to
            # switch on: we apply on/off, brightness, and color and let the
            # pipeline animations run.
            if data.get("object_id") != LIGHT_OBJECT_ID:
                return
            self._state.update(
                light_is_on=bool(data.get("state", True)),
                light_brightness=float(data.get("brightness", 0.66)),
                light_red=float(data.get("red", 0.094)),
                light_green=float(data.get("green", 0.733)),
                light_blue=float(data.get("blue", 0.949)),
            )
            self._animator.set_state(self._state.assist_state, force=True)
            return

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
    multipress  = ButtonMultipressHandler(state, loop, command_queue)

    client   = LVAClient(host, port, state, animator, command_queue)

    # Wire the button into the multipress detector so double, triple,
    # and long presses fire their sounds on LVA.
    buttons.setup(multipress)

    # Show "not ready" until LVA connects
    animator.set_state(AssistState.NOT_READY)

    def _shutdown(signum, frame) -> None:
        _LOGGER.info("Shutdown requested")
        multipress.cleanup()
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
        multipress.cleanup()
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
