#!/usr/bin/env python3
"""
ReSpeaker Mic Array v2.0 (USB) – Linux Voice Assistant peripheral controller.

Connects to the device via USB HID and drives its 12 APA102 LEDs with
animations that mirror the Home Assistant Voice PE LED ring behaviour.

Hardware
--------
  Connection : USB (Vendor 0x2886, Product 0x0018)
  LED ring   : 12 × APA102 RGB LEDs (controlled via USB HID)
  Microphones: 4 × MEMS mics at DOA 0°, 90°, 180°, 270° (XMOS XVF3000, USB audio)

LED animations mirror the ESPHome home-assistant-voice.yaml effects exactly.

Muted indicator: LEDs at the 4 mic positions light red.
The v2.0 has 4 mics at DOA 0°, 90°, 180°, 270° which on a 12-LED ring
(30° per step) maps to LEDs 0, 3, 6, 9 — the four cardinal points.

Install dependencies
--------------------
  pip install websockets pyusb pixel-ring

Host udev rule (run once on the Pi/host, then reboot or run udevadm trigger):
  echo 'SUBSYSTEM=="usb", ATTR{idVendor}=="2886", ATTR{idProduct}=="0018", \
        MODE="0666", GROUP="plugdev"' | sudo tee /etc/udev/rules.d/99-respeaker.rules
  sudo udevadm trigger

Run
---
  python3 respeaker_usb_mic_array.py
  python3 respeaker_usb_mic_array.py --host 127.0.0.1 --port 6055 --debug
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
# Hardware dependencies — gracefully stubbed when not on real hardware
# ---------------------------------------------------------------------------

try:
    import usb.core  # type: ignore[import]
    import usb.util  # type: ignore[import]
    _HAS_USB = True
except ImportError:
    _HAS_USB = False
    logging.warning("pyusb not found – LED output will be simulated")

try:
    import websockets  # type: ignore[import]
except ImportError:
    sys.exit("websockets not installed. Run: pip install websockets")


# ===========================================================================
# Configuration
# ===========================================================================

DEFAULT_LVA_HOST  = "localhost"
DEFAULT_LVA_PORT  = 6055

# USB device identifiers
USB_VENDOR_ID     = 0x2886
USB_PRODUCT_ID    = 0x0018

LED_COUNT         = 12
LED_BRIGHTNESS    = 10    # 0–31 (APA102 5-bit global brightness register)

RECONNECT_DELAY_S = 3.0


# ===========================================================================
# Logging
# ===========================================================================

_LOGGER = logging.getLogger("respeaker-usb")


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
# USB LED driver
#
# The ReSpeaker Mic Array v2.0 accepts LED data via a USB vendor control
# transfer.  Each LED is set as an APA102 frame: 0xFF start, then per-LED
# bytes in the order [start_frame | brightness, B, G, R].
# ===========================================================================

RGB = Tuple[int, int, int]

BLACK: RGB = (0,   0,   0)
RED:   RGB = (255, 0,   0)
BLUE:  RGB = (0,   0,   255)
CYAN:  RGB = (0,   200, 200)
GREEN: RGB = (0,   200, 50)
YELLOW: RGB = (220, 180, 0)


def _scale(color: RGB, factor: float) -> RGB:
    f = max(0.0, min(1.0, factor))
    return (int(color[0] * f), int(color[1] * f), int(color[2] * f))


class USBLEDRing:
    """
    Controls the 12 APA102 LEDs on the ReSpeaker Mic Array v2.0 over USB.

    Uses a USB vendor control transfer (bmRequestType=0x40, bRequest=0,
    wValue=0x0001, wIndex=0x8001) to push raw LED data to the XMOS firmware.
    This is the same protocol used by Seeed's pixel_ring library.
    """

    # USB control transfer parameters (from XMOS firmware)
    _CTRL_TIMEOUT   = 100   # ms
    _CTRL_REQUEST   = 0
    _CTRL_VALUE     = 0x0001
    _CTRL_INDEX     = 0x8001
    _CTRL_TYPE_OUT  = 0x40  # vendor, device, host-to-device

    def __init__(self) -> None:
        self._pixels: list[RGB] = [BLACK] * LED_COUNT
        self._dev = None
        self._dev_lock = threading.Lock()

        if _HAS_USB:
            self._find_device()

    def _find_device(self) -> None:
        dev = usb.core.find(idVendor=USB_VENDOR_ID, idProduct=USB_PRODUCT_ID)
        if dev is None:
            _LOGGER.error(
                "ReSpeaker Mic Array v2.0 not found (USB %04x:%04x). "
                "Check connection and udev rules.",
                USB_VENDOR_ID, USB_PRODUCT_ID,
            )
            return
        try:
            if dev.is_kernel_driver_active(0):
                dev.detach_kernel_driver(0)
        except Exception:  # pylint: disable=broad-except
            pass
        self._dev = dev
        _LOGGER.info(
            "ReSpeaker Mic Array v2.0 found (USB %04x:%04x, bus %d, addr %d)",
            USB_VENDOR_ID, USB_PRODUCT_ID, dev.bus, dev.address,
        )

    def set(self, index: int, color: RGB) -> None:
        self._pixels[index % LED_COUNT] = color

    def set_all(self, color: RGB) -> None:
        self._pixels = [color] * LED_COUNT

    def show(self, brightness: float = 1.0) -> None:
        """
        Push the current pixel buffer to the device.

        Frame layout (matches pixel_ring / APA102 USB protocol):
          [0xFF, 0xFF, 0xFF, 0xFF]            ← start frame
          [0xE0|bright5, B, G, R] × LED_COUNT ← one word per LED
          [0xFF, ...]                          ← end frame (⌈n/2⌉ bytes)
        """
        bright5 = max(0, min(31, int(brightness * LED_BRIGHTNESS)))

        frame = [0xFF, 0xFF, 0xFF, 0xFF]  # start frame
        for r, g, b in self._pixels:
            br = max(0, min(255, int(r * brightness)))
            bg = max(0, min(255, int(g * brightness)))
            bb = max(0, min(255, int(b * brightness)))
            frame += [0xE0 | bright5, bb, bg, br]
        frame += [0xFF] * math.ceil(LED_COUNT / 2)  # end frame

        with self._dev_lock:
            if self._dev is None:
                pixels = ", ".join(f"rgb{p}" for p in self._pixels)
                _LOGGER.debug("LEDs [simulated]: %s", pixels)
                return
            try:
                self._dev.ctrl_transfer(
                    self._CTRL_TYPE_OUT,
                    self._CTRL_REQUEST,
                    self._CTRL_VALUE,
                    self._CTRL_INDEX,
                    frame,
                    self._CTRL_TIMEOUT,
                )
            except usb.core.USBError as exc:
                _LOGGER.warning("USB write error: %s – attempting reconnect", exc)
                self._dev = None
                # Attempt to re-find the device on next show() call
                threading.Thread(
                    target=self._reconnect, daemon=True
                ).start()

    def _reconnect(self) -> None:
        time.sleep(2)
        _LOGGER.info("Attempting to reconnect to ReSpeaker USB device …")
        self._find_device()

    def off(self) -> None:
        self._pixels = [BLACK] * LED_COUNT
        self.show(0)

    def close(self) -> None:
        self.off()
        with self._dev_lock:
            if self._dev is not None:
                usb.util.dispose_resources(self._dev)
                self._dev = None


# ===========================================================================
# LED ring animator
#
# All animations mirror the ESPHome home-assistant-voice.yaml effects.
# Muted indicator uses LEDs 0, 2, 4, 6, 8, 10 — the 6 outer mic positions
# on the v2.0 (0°, 60°, 120°, 180°, 240°, 300° on the ring).
# ===========================================================================

# Mic LED positions — 4 mics at DOA 0°, 90°, 180°, 270°
# On a 12-LED ring (30° per step): DOA 0° → LED 0, 90° → LED 3,
#                                   180° → LED 6, 270° → LED 9
MIC_LEDS = (0, 3, 6, 9)


class LEDRing:
    """
    Manages the 12-LED ring animations for the ReSpeaker Mic Array v2.0.
    All animations run in a dedicated daemon thread.
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
        self._leds = USBLEDRing()
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

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        self._thread.join(timeout=2)
        self._leds.off()
        self._leds.close()

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

    def _color(self) -> RGB:
        """Default ring colour matching HA Voice PE."""
        return (24, 187, 242)

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

        ReSpeaker Mic Array v2.0 has 4 mics at DOA 0°, 90°, 180°, 270°.
        On the 12-LED ring (30° per step):
          DOA   0° → LED 0  (right)
          DOA  90° → LED 3  (top)
          DOA 180° → LED 6  (left)
          DOA 270° → LED 9  (bottom)
        Neighbours are blanked so each indicator reads as a distinct dot.
        """
        self._leds.set(11, BLACK); self._leds.set(0, RED);  self._leds.set(1, BLACK)
        self._leds.set(2,  BLACK); self._leds.set(3, RED);  self._leds.set(4, BLACK)
        self._leds.set(5,  BLACK); self._leds.set(6, RED);  self._leds.set(7, BLACK)
        self._leds.set(8,  BLACK); self._leds.set(9, RED);  self._leds.set(10, BLACK)

    def _apply_mic_indicators_pulsed(self, factor: float) -> None:
        """Pulsed version for timer_ring — keeps indicators in sync with ring brightness."""
        self._leds.set(0, _scale(RED, factor))
        self._leds.set(3, _scale(RED, factor))
        self._leds.set(6, _scale(RED, factor))
        self._leds.set(9, _scale(RED, factor))

    # ------------------------------------------------------------------
    # Animations — each returns sleep time in seconds
    # ------------------------------------------------------------------

    def _anim_off(self) -> float:
        self._leds.off()
        return 0.1

    def _anim_twinkle(self, color: RGB) -> float:
        FADE = 0.85
        SPARK_PROB = 0.15
        for i in range(LED_COUNT):
            if random.random() < SPARK_PROB:
                self._twinkle_state[i] = 1.0
            else:
                self._twinkle_state[i] *= FADE
            self._leds.set(i, _scale(color, self._twinkle_state[i]))
        self._leds.show()
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
            self._leds.set(i, BLACK)
        for offset, brightness in offsets:
            self._leds.set((self._index + offset) % LED_COUNT, _scale(color, brightness))
        if not reverse:
            self._index = (self._index + 1) % LED_COUNT

        self._leds.show()
        return interval

    def _anim_thinking(self, color: RGB) -> float:
        """Two opposing LEDs pulsing. Mirrors ESPHome "Thinking" effect."""
        factor = self._pulse_step(10)
        for i in range(LED_COUNT):
            if i == self._index % LED_COUNT or i == (self._index + 6) % LED_COUNT:
                self._leds.set(i, _scale(color, factor))
            else:
                self._leds.set(i, BLACK)
        self._leds.show()
        return 0.01

    def _anim_muted(self, color: RGB) -> float:
        """
        Solid ring with red at all 4 mic positions (cardinal points).
        DOA 0°/90°/180°/270° → LEDs 0, 3, 6, 9.
        Neighbours are blanked so each indicator stands out clearly.
        Mirrors ESPHome "Muted or Silent" effect.
        """
        for i in range(LED_COUNT):
            self._leds.set(i, color)
        self._apply_mic_indicators()
        self._leds.show()
        return 0.016

    def _anim_error(self) -> float:
        """All LEDs red, pulsing. Mirrors ESPHome "Error" effect."""
        factor = self._pulse_step(10)
        for i in range(LED_COUNT):
            self._leds.set(i, _scale(RED, factor))
        self._leds.show()
        return 0.01

    def _anim_timer_ring(self, color: RGB, muted: bool) -> float:
        """
        Full ring pulsing; red at all 6 mic positions if muted.
        Mirrors ESPHome "Timer Ring" effect.
        """
        factor = self._pulse_step(10)
        for i in range(LED_COUNT):
            self._leds.set(i, _scale(color, factor))
        if muted:
            self._apply_mic_indicators_pulsed(factor)
        self._leds.show()
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
                self._leds.set(i, _scale(color, brightness))
            else:
                self._leds.set(i, BLACK)

        if muted:
            self._apply_mic_indicators()

        self._index = (LED_COUNT + self._index - 1) % LED_COUNT
        self._leds.show()
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
# State → animation mapping (identical to 4-Mic HAT)
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

    leds   = LEDRing(state)
    client = LVAClient(host, port, state, leds, command_queue)

    leds.start()
    leds.set_animation(LEDRing.ANIM_TWINKLE)

    def _shutdown(signum, frame) -> None:
        _LOGGER.info("Shutdown requested")
        leds.stop()
        sys.exit(0)

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    _LOGGER.info(
        "ReSpeaker Mic Array v2.0 controller started – connecting to ws://%s:%d",
        host, port,
    )

    try:
        await client.run_forever()
    finally:
        leds.stop()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="ReSpeaker Mic Array v2.0 (USB) controller for Linux Voice Assistant"
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