#!/usr/bin/env python3
"""
Satellite 1 HAT Board – Linux Voice Assistant peripheral controller.

Mirrors the Home Assistant Voice PE LED ring animations and maps the four
hardware buttons to LVA peripheral API commands.

Hardware layout (Satellite 1 HAT on Raspberry Pi)
--------------------------------------------------
  LED ring   : 12 × SK6812 RGBW NeoPixels  →  GPIO 12 (PWM0)
  Right btn  : Volume Up                    →  GPIO 17
  Left btn   : Volume Down                  →  GPIO 27
  Top btn    : Mute / Unmute mic            →  GPIO 22
  Bottom btn : Context action               →  GPIO 23
                idle            → start_listening
                listening       → stop_pipeline
                thinking        → stop_pipeline
                tts_speaking    → stop_speaking
                timer_ringing   → stop_timer_ringing
                media_playing   → stop_media_player

Install dependencies
---------------------
  pip install websockets rpi-ws281x gpiozero lgpio

Run
---
  python3 Satellite1_HAT_Board.py
  python3 Satellite1_HAT_Board.py --host 127.0.0.1 --port 6055 --debug
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
from typing import List, Optional, Tuple

# ---------------------------------------------------------------------------
# Hardware dependencies (gracefully stubbed when not on real Pi hardware so
# the script can be imported / syntax-checked on a dev machine)
# ---------------------------------------------------------------------------

try:
    from gpiozero import Button  # type: ignore[import]
    _HAS_GPIO = True
except ImportError:
    _HAS_GPIO = False
    logging.warning("gpiozero not found – button input disabled")

try:
    from rpi_ws281x import PixelStrip, Color as _NeoColor  # type: ignore[import]
    _HAS_NEOPIXEL = True
except ImportError:
    _HAS_NEOPIXEL = False
    logging.warning("rpi_ws281x not found – LED output disabled")

try:
    import websockets  # type: ignore[import]
except ImportError:
    sys.exit("websockets not installed. Run: pip install websockets")


# ===========================================================================
# Configuration – edit these to match your wiring
# ===========================================================================

DEFAULT_LVA_HOST = "localhost"
DEFAULT_LVA_PORT = 6055

# GPIO (BCM numbering)
LED_GPIO_PIN    = 12
BTN_VOLUME_UP   = 17   # Right button
BTN_VOLUME_DOWN = 27   # Left button
BTN_MUTE        = 22   # Top button
BTN_ACTION      = 23   # Bottom button

BTN_DEBOUNCE_MS = 150  # Milliseconds

# LED ring
LED_COUNT      = 12
LED_FREQ_HZ    = 800_000
LED_DMA        = 10
LED_BRIGHTNESS = 168   # 66 % of 255 – matches ESPHome default
LED_INVERT     = False
LED_CHANNEL    = 0

# Default ring color  (ESPHome default: 9.4 % R, 73.3 % G, 94.9 % B)
DEFAULT_R, DEFAULT_G, DEFAULT_B = 24, 187, 242

# Reconnect delay when WebSocket connection to LVA is lost
RECONNECT_DELAY_S = 3.0


# ===========================================================================
# Logging
# ===========================================================================

_LOGGER = logging.getLogger("satellite1")


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
                "assist_state":         self.assist_state,
                "ha_connected":         self.ha_connected,
                "muted":                self.muted,
                "volume":               self.volume,
                "timer_total_seconds":  self.timer_total_seconds,
                "timer_seconds_left":   self.timer_seconds_left,
            }


# ===========================================================================
# LED ring controller
# Mirrors every animation from the ESPHome home-assistant-voice.yaml exactly.
# ===========================================================================

RGB = Tuple[int, int, int]

BLACK: RGB = (0, 0, 0)
RED:   RGB = (255, 0, 0)


def _scale(color: RGB, factor: float) -> RGB:
    """Scale an RGB colour by 0.0–1.0."""
    f = max(0.0, min(1.0, factor))
    return (int(color[0] * f), int(color[1] * f), int(color[2] * f))


def _neo(color: RGB) -> int:
    """Convert (r, g, b) to a rpi_ws281x 24-bit integer."""
    if _HAS_NEOPIXEL:
        return _NeoColor(color[0], color[1], color[2])
    return 0


class LEDRing:
    """
    Manages the 12-LED NeoPixel ring.

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
        self._pixels: List[RGB] = [BLACK] * LED_COUNT
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
        self._twinkle_state: List[float] = [0.0] * LED_COUNT

        if _HAS_NEOPIXEL:
            self._strip = PixelStrip(
                LED_COUNT, LED_GPIO_PIN, LED_FREQ_HZ,
                LED_DMA, LED_INVERT, LED_BRIGHTNESS, LED_CHANNEL,
            )
            self._strip.begin()
            _LOGGER.info(
                "LED ring initialised (%d LEDs on GPIO %d)", LED_COUNT, LED_GPIO_PIN
            )

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        self._thread.join(timeout=2)
        self._all_off()

    def set_animation(self, name: str) -> None:
        with self._anim_lock:
            if self._animation != name:
                _LOGGER.debug("LED animation → %s", name)
                self._animation = name
                # Reset counters on animation change (mirrors ESPHome initial_run)
                self._index = 0
                self._brightness_step = 0
                self._brightness_dec = True
                self._twinkle_state = [random.random() for _ in range(LED_COUNT)]

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _write(self) -> None:
        if not _HAS_NEOPIXEL or self._strip is None:
            return
        for i, color in enumerate(self._pixels):
            self._strip.setPixelColor(i, _neo(color))
        self._strip.show()

    def _all_off(self) -> None:
        self._pixels = [BLACK] * LED_COUNT
        self._write()

    def _set(self, i: int, color: RGB) -> None:
        self._pixels[i % LED_COUNT] = color

    def _color(self) -> RGB:
        """Return the current user-configured ring colour."""
        return (DEFAULT_R, DEFAULT_G, DEFAULT_B)

    def _pulse_step(self, steps: int = 10) -> float:
        """Advance pulse counter and return brightness 0.0–1.0."""
        factor = (steps - self._brightness_step) / steps
        if self._brightness_dec:
            self._brightness_step += 1
        else:
            self._brightness_step -= 1
        if self._brightness_step <= 0 or self._brightness_step >= steps:
            self._brightness_dec = not self._brightness_dec
        return factor

    # ------------------------------------------------------------------
    # Animation implementations  (each returns sleep time in seconds)
    # All logic mirrors the ESPHome addressable_lambda effects verbatim.
    # ------------------------------------------------------------------

    def _anim_off(self) -> float:
        self._all_off()
        return 0.1

    def _anim_twinkle(self, color: RGB) -> float:
        """Random sparkle – used for Not Ready and No HA connection states."""
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
        Shared spin pattern for Waiting / Listening / Replying.

        Mirrors the ESPHome "Waiting for Command", "Listening For Command"
        and "Replying" addressable_lambda effects.
        """
        if reverse:
            # Replying goes anticlockwise: index decrements
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
        """
        Two opposing LEDs pulsing in brightness.
        Mirrors ESPHome "Thinking" effect. Index does NOT advance.
        """
        factor = self._pulse_step(10)
        for i in range(LED_COUNT):
            if i == self._index % LED_COUNT or i == (self._index + 6) % LED_COUNT:
                self._set(i, _scale(color, factor))
            else:
                self._set(i, BLACK)
        self._write()
        return 0.01

    def _anim_muted(self, color: RGB, muted: bool) -> float:
        """
        Solid ring with red indicators at all 4 mic positions when muted.
        Satellite 1 HAT has mics at 12, 3, 6 and 9 o'clock → LEDs 0, 3, 6, 9.
        """
        for i in range(LED_COUNT):
            self._set(i, color)
          
        if muted:
            # 4 mic positions: top (0), right (3), bottom (6), left (9)
            # Blank the immediate neighbours so the red indicators stand out          
            self._set(11, BLACK); self._set(0, RED);  self._set(1, BLACK)
            self._set(2,  BLACK); self._set(3, RED);  self._set(4, BLACK)
            self._set(5,  BLACK); self._set(6, RED);  self._set(7, BLACK)
            self._set(8,  BLACK); self._set(9, RED);  self._set(10, BLACK)

        self._write()
        return 0.016

    def _anim_error(self) -> float:
        """
        All LEDs red, pulsing. Mirrors ESPHome "Error" effect.
        """      
        factor = self._pulse_step(10)
        for i in range(LED_COUNT):
            self._set(i, _scale(RED, factor))
        self._write()
        return 0.01

    def _anim_timer_ring(self, color: RGB, muted: bool) -> float:
        """
        All LEDs pulse with ring colour; red at all 4 mic positions if muted.
        Mirrors ESPHome "Timer Ring" effect.
        """      
        factor = self._pulse_step(10)
        for i in range(LED_COUNT):
            self._set(i, _scale(color, factor))
        if muted:
            # 4 mic positions: LEDs 0, 3, 6, 9
            self._set(0, _scale(RED, factor))
            self._set(3, _scale(RED, factor))
            self._set(6, _scale(RED, factor))
            self._set(9, _scale(RED, factor))
        self._write()
        return 0.01

    def _anim_timer_tick(
        self, color: RGB, muted: bool,
        seconds_left: int, total_seconds: int,
    ) -> float:
        """
        Arc of LEDs proportional to remaining time.
        Mirrors ESPHome "Timer Tick" effect exactly, including the
        brightness-dip on the sweep marker LED.
        """      
        total = max(total_seconds, 1)
        timer_ratio = LED_COUNT * seconds_left / total
        last_led_on = max(0, math.ceil(timer_ratio) - 1)

        # Index sweeps anticlockwise (matches ESPHome anticlockwise decrement)
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
            # 4 mic positions: LEDs 0, 3, 6, 9
            self._set(11, BLACK); self._set(0, RED);  self._set(1, BLACK)
            self._set(2,  BLACK); self._set(3, RED);  self._set(4, BLACK)
            self._set(5,  BLACK); self._set(6, RED);  self._set(7, BLACK)
            self._set(8,  BLACK); self._set(9, RED);  self._set(10, BLACK)

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

            snap         = self._state.snapshot
            color        = self._color()
            muted        = snap["muted"]
            t_total      = snap["timer_total_seconds"]
            t_left       = snap["timer_seconds_left"]

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
                sleep = self._anim_muted(color, muted=True)
              
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

def state_to_animation(state: AssistState, ha_connected: bool, muted: bool) -> str:
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
# Button handler — uses gpiozero (works on Pi 3/4/5 without RPi.GPIO)
# ===========================================================================

class ButtonHandler:
    """
    Manages the four hardware buttons using gpiozero.

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
        self._buttons: List = []

    def setup(self) -> None:
        if not _HAS_GPIO:
            _LOGGER.warning("GPIO unavailable – buttons not configured")
            return

        debounce = BTN_DEBOUNCE_MS / 1000.0

        btn_up   = Button(BTN_VOLUME_UP,   pull_up=True, bounce_time=debounce)
        btn_down = Button(BTN_VOLUME_DOWN, pull_up=True, bounce_time=debounce)
        btn_mute = Button(BTN_MUTE,        pull_up=True, bounce_time=debounce)
        btn_act  = Button(BTN_ACTION,      pull_up=True, bounce_time=debounce)

        btn_up.when_pressed   = self._on_volume_up
        btn_down.when_pressed = self._on_volume_down
        btn_mute.when_pressed = self._on_mute
        btn_act.when_pressed  = self._on_action

        # Keep references — gpiozero Buttons are released when garbage-collected
        self._buttons = [btn_up, btn_down, btn_mute, btn_act]
        _LOGGER.info(
            "Buttons configured (gpiozero BCM %d/%d/%d/%d)",
            BTN_VOLUME_UP, BTN_VOLUME_DOWN, BTN_MUTE, BTN_ACTION,
        )

    def cleanup(self) -> None:
        for btn in self._buttons:
            btn.close()
        self._buttons.clear()

    def _send(self, command: str) -> None:
        _LOGGER.info("Button → %s", command)
        asyncio.run_coroutine_threadsafe(
            self._queue.put(command), self._loop
        )

    def _on_volume_up(self) -> None:
        self._send("volume_up")

    def _on_volume_down(self) -> None:
        self._send("volume_down")

    def _on_mute(self) -> None:
        if self._state.muted:
            self._send("unmute_mic")
        else:
            self._send("mute_mic")

    def _on_action(self) -> None:
        """
        Context-sensitive bottom button — mirrors HA Voice PE priority:
          1. Timer ringing           → stop_timer_ringing
          2. Pipeline active         → stop_pipeline
          3. TTS speaking            → stop_speaking
          4. Media playing           → stop_media_player
          5. Idle / anything else    → start_listening
        """
        assist = self._state.assist_state

        if assist == AssistState.TIMER_RINGING:
            self._send("stop_timer_ringing")
        elif assist in (AssistState.WAKE_WORD, AssistState.LISTENING, AssistState.THINKING):
            # stop_pipeline aborts the voice pipeline at any of these phases
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
    """
    Maintains a persistent WebSocket connection to the LVA peripheral API.

    - Receives events and updates SharedState + LEDRing.
    - Drains command_queue and sends commands.
    """  

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
            # Re-raise the first exception so run_forever() can handle it
            for task in done:
                if not task.cancelled() and task.exception():
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
                # Put it back so it's not silently dropped
                self._queue.put_nowait(command)
                raise

    async def _handle_event(self, msg: dict) -> None:
        event = msg.get("event", "")
        data  = msg.get("data") or {}

        _LOGGER.debug("Event: %s  data=%s", event, data)

        # --- Snapshot (sent on connect) ------------------------------------
        if event == "snapshot":
            self._state.update(
                muted=data.get("muted", False),
                volume=data.get("volume", 1.0),
                ha_connected=data.get("ha_connected", False),
            )
        # --- Voice pipeline events -----------------------------------------
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

        # --- Timer events --------------------------------------------------      
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
          
        # --- Media / volume events -----------------------------------------          
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

        # --- Recompute LED animation after every event ---------------------
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
    buttons = ButtonHandler(state, loop, command_queue)
    client  = LVAClient(host, port, state, leds, command_queue)

    # Start hardware
    leds.start()
    buttons.setup()

    # Start with "not ready" animation
    leds.set_animation(LEDRing.ANIM_TWINKLE)

    # Graceful shutdown on SIGINT / SIGTERM
    def _shutdown(signum, frame) -> None:
        _LOGGER.info("Shutdown requested")
        leds.stop()
        buttons.cleanup()
        sys.exit(0)

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    _LOGGER.info(
        "Satellite 1 HAT controller started – connecting to ws://%s:%d",
        host, port,
    )

    try:
        await client.run_forever()
    finally:
        leds.stop()
        buttons.cleanup()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Satellite 1 HAT Board controller for Linux Voice Assistant"
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
