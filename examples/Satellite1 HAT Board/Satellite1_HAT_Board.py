#!/usr/bin/env python3
"""
Satellite 1 HAT Board – Linux Voice Assistant peripheral controller.

Mirrors the Home Assistant Voice PE LED ring animations and maps the four
hardware buttons to LVA peripheral API commands.

Hardware layout (Satellite 1 HAT on Raspberry Pi)
--------------------------------------------------
  LED ring   : 24 x WS2812 RGBW NeoPixels  →  GPIO 12 (PWM0), see the note below
  Buttons    : read from the XMOS XVF3800 over SPI (/dev/spidev0.0), NOT from
               Raspberry Pi GPIOs. They are bits of the XMOS status register:
                 bit 0  Right btn  : Volume Up      (momentary, active low)
                 bit 1  Bottom btn : Context action (momentary, active low)
                 bit 2  Left btn   : Volume Down    (momentary, active low)
                 bit 3  Top btn    : Mute / Unmute  (latching switch, 1 = muted)
                Bottom button, single press:
                    idle            → start_listening
                    listening       → stop_pipeline
                    thinking        → stop_pipeline
                    tts_speaking    → stop_pipeline
                    timer_ringing   → stop_timer_ringing
                    media_playing   → stop_media_player
                Bottom button, gestures (detected via timing):
                    double press (< 250ms between releases)  → button_double_press
                    triple press (< 250ms between releases)  → button_triple_press
                    long press (held > 1000ms)               → button_long_press

LED ring wiring note (Raspberry Pi)
-----------------------------------
  The HAT routes the ring's data line to header pin 15 (BCM GPIO 22), which has
  no PWM/PCM/SPI function, so rpi_ws281x cannot drive it directly. Fit a jumper
  from header pin 15 to header pin 32 (BCM GPIO 12, PWM0) and leave GPIO 22 as
  an input. GPIO 22 must not be claimed by any other software.

  rpi_ws281x also needs a DMA channel that the kernel is not using. On the
  Raspberry Pi Zero 2 W the kernel dmaengine already uses channel 10, which
  freezes the ring under audio load, so the default is channel 14. List the
  channels the kernel manages with:
    od -An -tx1 /proc/device-tree/soc/dma-controller@7e007000/brcm,dma-channel-mask
  and pick a channel that is rarely allocated (e.g. the highest one) with
  --led-dma.

Install dependencies
---------------------
  pip install websockets rpi-ws281x spidev

  Also enable SPI on the host (dtparam=spi=on in config.txt).

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
from typing import Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# Hardware dependencies (gracefully stubbed when not on real Pi hardware so
# the script can be imported / syntax-checked on a dev machine)
# ---------------------------------------------------------------------------

try:
    import spidev  # type: ignore[import]
    _HAS_SPI = True
except ImportError:
    _HAS_SPI = False
    logging.warning("spidev not found – button input disabled")

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

# GPIO (BCM numbering). The LED ring data line reaches the Pi on GPIO 22 and
# must be jumpered to GPIO 12 (PWM0) — see the module docstring.
LED_GPIO_PIN   = 12

# Buttons are read from the XMOS XVF3800 over SPI, not from Pi GPIOs. A NO-OP
# control frame makes the XMOS return its 4-byte status register in the same
# transfer; the buttons are bits of its gpio_port_a byte.
SPI_BUS         = 0
SPI_DEVICE      = 0
SPI_MODE        = 3
SPI_SPEED_HZ    = 500_000    # The XMOS supports up to 1 MHz
XMOS_NOOP_FRAME = (0x00, 0x00, 0x00, 0x00, 0x00, 0x00)  # 3 header + 3 dummy bytes
XMOS_RESPONSE_ID = 0x01      # CNTRL_RES_ID in rx[0] of a valid response
XMOS_RESPONSE_BUSY = 0x17    # rx[1] value meaning no status is returned
XMOS_PORT_A_INDEX = 3        # gpio_port_a = rx[3]

# Bit numbers inside gpio_port_a. Idle reads 0b0111: bits 0-2 are active low
# (0 = pressed); bit 3 is a latching switch (1 = muted).
BTN_BIT_VOLUME_UP   = 0   # Right button
BTN_BIT_ACTION      = 1   # Bottom button
BTN_BIT_VOLUME_DOWN = 2   # Left button
BTN_BIT_MUTE        = 3   # Top button

BTN_POLL_INTERVAL_S = 0.025   # ~40 Hz; a change must be read twice in a row
                              # to count, which debounces the contacts

# Action button gestures
MULTIPRESS_WINDOW_MS = 250    # Max time between releases of a multi-press
LONG_PRESS_MS        = 1000   # Hold time that counts as a long press
ACTION_HOLD_MS       = 100    # Hold time that enters color change mode

# Continuous hold-repeat behaviour for the volume buttons (and, while the
# action button is held at the same time, the color wheel rotation). Once a
# volume button has been held past VOLUME_HOLD_THRESHOLD_MS, the associated
# step (volume up/down, or hue up/down when in color change mode) repeats
# every VOLUME_HOLD_REPEAT_S seconds until the button is released.
VOLUME_HOLD_THRESHOLD_MS = 1000  # Milliseconds before continuous repeat starts
VOLUME_HOLD_REPEAT_S = 0.15  # Seconds between repeated steps while held

# LED ring
LED_COUNT      = 24
LED_FREQ_HZ    = 800_000
LED_DMA        = 14    # Not 10: the kernel also uses channel 10 on the Pi Zero 2 W
LED_BRIGHTNESS = 168   # 66 % of 255 – matches ESPHome default
LED_INVERT     = False
LED_CHANNEL    = 0

# Four mic positions at the cardinal points (top, right, bottom, left),
# spaced LED_COUNT/4 apart. On the 24-LED ring: LEDs 0, 6, 12, 18.
MIC_LED_POSITIONS = tuple(i * (LED_COUNT // 4) for i in range(4))

# LEDs physically sitting in the AUX (3.5 mm) audio jack corner of the board,
# identified by their silkscreen designators on the Satellite1 HAT (D35, D1,
# D2). These three ring positions light up solid red whenever the media
# player volume is at zero (the `volume_muted` peripheral event), independent
# of whatever pipeline animation is currently running. Scaled to the 24-LED
# ring to cover the same angular span as the original 12-LED mapping —
# verify against your board revision and adjust if it differs.
AUX_JACK_LED_INDICES = (22, 23, 0, 1, 2)

# Volume Display effect
# Mirrors the ESPHome Voice PE "Volume Display" addressable_lambda, but
# rotated so the arc originates at the AUX jack corner LED (D1, index 0)
# instead of Voice PE's origin — matching this board's physical AUX jack
# placement. Shown as a temporary overlay for this many seconds after
# volume_changed.
VOLUME_DISPLAY_SECONDS = 2.5

# Default ring color  (ESPHome default: 9.4 % R, 73.3 % G, 94.9 % B)
DEFAULT_R, DEFAULT_G, DEFAULT_B = 24, 187, 242

# HA Light entity registered with LVA via register_light. The same object_id
# is used to route incoming light_command events back to this script.
LIGHT_OBJECT_ID = "led_ring"
LIGHT_NAME      = "LED Ring"
LIGHT_ICON      = "mdi:circle-outline"
EFFECT_VOICE_ASSISTANT = "Voice Assistant"

# Reconnect delay when WebSocket connection to LVA is lost
RECONNECT_DELAY_S = 3.0

# Color wheel parameters (matches Home Assistant Voice PE)
# HSV hue rotation: ±10° per volume button press
HUE_INCREMENT = 10  # Degrees
DEFAULT_HUE = 195   # Cyan (matches default RGB 24, 187, 242)
DEFAULT_SATURATION = 1.0  # 100%
DEFAULT_VALUE = 1.0  # 100%


# ===========================================================================
# Color conversion utilities (HSV ↔ RGB)
# ===========================================================================

def hsv_to_rgb(hue: int, saturation: float, value: float) -> Tuple[int, int, int]:
    """
    Convert HSV (Hue: 0-360, Saturation: 0-1, Value: 0-1) to RGB (0-255).
    Mirrors the Home Assistant Voice PE color wheel.
    """
    # Normalize hue to 0-360 range
    hue = hue % 360
    
    # Clamp saturation and value to 0-1
    sat = max(0.0, min(1.0, saturation))
    val = max(0.0, min(1.0, value))
    
    # Ensure saturation is at least 5% to avoid desaturated colors
    if sat < 0.05:
        sat = 1.0
    
    c = val * sat  # Chroma
    h_prime = hue / 60.0
    x = c * (1.0 - abs(h_prime % 2.0 - 1.0))
    
    if h_prime < 1:
        r_prime, g_prime, b_prime = c, x, 0.0
    elif h_prime < 2:
        r_prime, g_prime, b_prime = x, c, 0.0
    elif h_prime < 3:
        r_prime, g_prime, b_prime = 0.0, c, x
    elif h_prime < 4:
        r_prime, g_prime, b_prime = 0.0, x, c
    elif h_prime < 5:
        r_prime, g_prime, b_prime = x, 0.0, c
    else:
        r_prime, g_prime, b_prime = c, 0.0, x
    
    m = val - c
    r = int((r_prime + m) * 255)
    g = int((g_prime + m) * 255)
    b = int((b_prime + m) * 255)
    
    return (r, g, b)


def rgb_to_hsv(r: int, g: int, b: int) -> Tuple[int, float, float]:
    """
    Convert RGB (0-255) to HSV (Hue: 0-360, Saturation: 0-1, Value: 0-1).
    """
    r_norm = r / 255.0
    g_norm = g / 255.0
    b_norm = b / 255.0
    
    max_c = max(r_norm, g_norm, b_norm)
    min_c = min(r_norm, g_norm, b_norm)
    delta = max_c - min_c
    
    # Value
    value = max_c
    
    # Saturation
    if max_c == 0:
        saturation = 0.0
    else:
        saturation = delta / max_c
    
    # Hue
    if delta == 0:
        hue = 0
    elif max_c == r_norm:
        hue = (60 * ((g_norm - b_norm) / delta) + 360) % 360
    elif max_c == g_norm:
        hue = (60 * ((b_norm - r_norm) / delta) + 120) % 360
    else:  # max_c == b_norm
        hue = (60 * ((r_norm - g_norm) / delta) + 240) % 360
    
    return (int(hue), saturation, value)


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
    VOLUME_MUTED  = "volume_muted"


# ---------------------------------------------------------------------------
# Shared state (written by WS thread, read by LED thread + button callbacks)
# ---------------------------------------------------------------------------

class SharedState:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.assist_state: AssistState = AssistState.NOT_READY
        self.ha_connected: bool = False
        self.muted: bool = False  # mic mute
        self.volume: float = 1.0
        # Monotonic deadline until which the Volume Display arc takes over
        # the ring, set on each volume_changed event. 0 (or in the past)
        # means the arc is not showing.
        self.volume_display_until: float = 0.0        
        self.volume_muted: bool = False  # media player volume zero
        self.timer_total_seconds: int = 0
        self.timer_seconds_left: int = 0
        # Light entity state, driven by HA via light_command events.
        # Defaults match the LEDLightEntity in LVA core so the script
        # behaves sensibly before the first light_command arrives: off by
        # default, so idle stays dark until the user turns the light on.
        self.light_is_on: bool = False
        self.light_brightness: float = 0.66
        self.light_red: float = 0.094
        self.light_green: float = 0.733
        self.light_blue: float = 0.949
        # HSV color tracking for hue rotation via volume buttons
        self.hsv_hue: int = DEFAULT_HUE
        self.hsv_saturation: float = DEFAULT_SATURATION
        self.hsv_value: float = DEFAULT_VALUE
        # True for as long as the action (bottom) button is physically held
        # down, regardless of press duration. Drives the "light up while
        # pressed" tactile feedback in LEDRing (only applied when the HA
        # Light entity is off — see LEDRing._anim_action_held).
        self.action_button_down: bool = False
        self.buttons_locked: bool = False

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
                "volume_display_until": self.volume_display_until,
                "volume_muted":         self.volume_muted,
                "timer_total_seconds":  self.timer_total_seconds,
                "timer_seconds_left":   self.timer_seconds_left,
                "light_is_on":          self.light_is_on,
                "light_brightness":     self.light_brightness,
                "light_red":            self.light_red,
                "light_green":          self.light_green,
                "light_blue":           self.light_blue,
                "hsv_hue":              self.hsv_hue,
                "hsv_saturation":       self.hsv_saturation,
                "hsv_value":            self.hsv_value,
                "action_button_down":   self.action_button_down,
                "buttons_locked":       self.buttons_locked,
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
    Manages the 24-LED NeoPixel ring.

    All animations run in a dedicated daemon thread. Calling
    ``set_animation()`` switches cleanly to the new pattern.
    """

    # Animation names mirror ESPHome effect names for easy cross-reference
    ANIM_IDLE            = "idle"             # Idle with light color when on
    ANIM_OFF             = "off"
    ANIM_TWINKLE         = "twinkle"          # Not-ready / no HA
    ANIM_TWINKLE_BLUE    = "twinkle_blue"     # Init / connecting
    ANIM_WAIT_CMD        = "waiting"          # Wake word detected (slow spin)
    ANIM_LISTENING       = "listening"        # STT active (fast spin)
    ANIM_THINKING        = "thinking"         # Intent processing (pulse pair)
    ANIM_REPLYING        = "replying"         # TTS speaking (reverse spin)
    ANIM_MUTED           = "muted"            # Muted indicators
    ANIM_ERROR           = "error"            # Red pulse
    ANIM_TIMER_TICK      = "timer_tick"       # Countdown arc
    ANIM_TIMER_RING      = "timer_ring"       # Ringing pulse

    def __init__(self, state: SharedState, dma: int = LED_DMA) -> None:
        """
        :param state: Shared assistant state.
        :param dma: DMA channel used by rpi_ws281x; must not be one the kernel
            is using (see the module docstring).
        """
        self._state = state
        self._pixels: List[RGB] = [BLACK] * LED_COUNT
        self._strip = None
        self._animation = self.ANIM_OFF
        self._anim_lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread = threading.Thread(
            target=self._loop, daemon=True, name="led-ring"
        )

        # Per-animation internal counters (reset on animation change)
        self._index: int = 0
        self._brightness_step: int = 0
        self._brightness_dec: bool = True
        self._twinkle_state: List[float] = [0.0] * LED_COUNT

        if _HAS_NEOPIXEL:
            self._strip = PixelStrip(
                LED_COUNT, LED_GPIO_PIN, LED_FREQ_HZ,
                dma, LED_INVERT, LED_BRIGHTNESS, LED_CHANNEL,
            )
            self._strip.begin()
            _LOGGER.info(
                "LED ring initialised (%d LEDs on GPIO %d, DMA channel %d)",
                LED_COUNT, LED_GPIO_PIN, dma,
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
        """Return the current user-configured ring colour from light entity state."""
        snap = self._state.snapshot
        return (
            int(max(0.0, min(1.0, snap["light_red"])) * 255),
            int(max(0.0, min(1.0, snap["light_green"])) * 255),
            int(max(0.0, min(1.0, snap["light_blue"])) * 255),
        )

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

    def _anim_idle(self) -> float:
        """Resting state with user color when light is on, else dark."""
        snap = self._state.snapshot
        if snap["light_is_on"]:
            # Show user color at configured brightness
            color = self._color()
            brightness = snap["light_brightness"]
            for i in range(LED_COUNT):
                self._set(i, _scale(color, brightness))
            self._write()
        else:
            self._all_off()
        return 0.1

    def _anim_action_held(self, color: RGB, brightness: float) -> float:
        """
        Force the ring fully on at the current (or default, if none has been
        chosen yet) light color while the action button is physically held
        down and the HA Light entity is off.

        Only takes over when the Light entity is off — if it's already on,
        the idle animation is already showing the color and the ring is
        left alone. Gives instant visual feedback for every press,
        including a very short tap (turns off the moment the button is
        released), and refreshes quickly so that rotating the color wheel
        (holding action + a volume button) shows up on the ring in real
        time.
        """
        for i in range(LED_COUNT):
            self._set(i, _scale(color, brightness))
        self._write()
        return 0.05

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
        # The second arc sits on the opposite side of the ring (half the LED
        # count away), as in the 12-LED Voice PE original.
        half = LED_COUNT // 2
        if reverse:
            # Replying goes anticlockwise: index decrements
            self._index = (LED_COUNT + self._index - 1) % LED_COUNT
            offsets = [(0, 1.0), (1, 192/255), (2, 128/255),
                       (half, 1.0), (half + 1, 192/255), (half + 2, 128/255)]
        else:
            offsets = [(0, 1.0), (LED_COUNT - 1, 192/255), (LED_COUNT - 2, 128/255),
                       (half, 1.0), (half - 1, 192/255), (half - 2, 128/255)]

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
            if i == self._index % LED_COUNT or i == (self._index + LED_COUNT // 2) % LED_COUNT:
                self._set(i, _scale(color, factor))
            else:
                self._set(i, BLACK)
        self._write()
        return 0.01

    def _anim_muted(self, color: RGB, muted: bool) -> float:
        """
        Solid ring with red indicators at all 4 mic positions when muted.
        """
        for i in range(LED_COUNT):
            self._set(i, color)

        if muted:
            self._apply_mic_indicators()

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
            # Red at the 4 mic positions (LEDs 0, 6, 12, 18 on the 24-LED ring)
            self._apply_mic_indicators_pulsed(factor)
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
            self._apply_mic_indicators()

        self._index = (LED_COUNT + self._index - 1) % LED_COUNT
        self._write()
        return 0.1

    def _anim_volume_display(self, color: RGB, volume: float) -> float:
        """
        Arc showing the current volume level, originating at the AUX jack
        corner LED (D1, index 0) and sweeping clockwise around the ring.

        Mirrors the ESPHome Voice PE "Volume Display" addressable_lambda
        exactly, except the arc's origin is rotated from LED 6 to LED 0 to
        match this board's AUX jack corner LED placement.
        """
        silenced_color = RED
        volume_ratio = LED_COUNT * max(0.0, min(1.0, volume))

        for i in range(LED_COUNT):
            if i <= volume_ratio:
                brightness = min(volume_ratio - i, 1.0)
                self._set(i, _scale(color, brightness))
            else:
                self._set(i, BLACK)

        if volume <= 0.0:
            self._set(0, silenced_color)

        self._write()
        return 0.05  # matches the 50ms update_interval of the ESPHome effect

    def _apply_mic_indicators(self) -> None:
        """
        Mark all 4 mic positions (cardinal points) red on the already
        rendered frame, blanking immediate neighbours so each indicator
        reads as a distinct dot. Positions are LED_COUNT/4 apart —
        LEDs 0, 6, 12, 18 on the 24-LED ring.
        """
        for pos in MIC_LED_POSITIONS:
            self._set((pos - 1) % LED_COUNT, BLACK)
            self._set(pos, RED)
            self._set((pos + 1) % LED_COUNT, BLACK)

    def _apply_mic_indicators_pulsed(self, factor: float) -> None:
        """Pulsed version for timer_ring — keeps indicators in sync with ring brightness."""
        for pos in MIC_LED_POSITIONS:
            self._set(pos, _scale(RED, factor))

    def _apply_volume_muted_indicator(self) -> None:
        """
        Overlay a solid red indicator on the AUX jack corner LEDs
        (designators D35, D1, D2 — see ``AUX_JACK_LED_INDICES``) when the
        media player output is muted.

        Applied as a post-render overlay after the current pipeline
        animation has already written its frame, so the indicator shows up
        consistently no matter which animation is active, without having to
        thread the flag through every individual ``_anim_*`` method.
        """
        for i in AUX_JACK_LED_INDICES:
            self._set(i, RED)
        self._write()

    # ------------------------------------------------------------------
    # Animation loop
    # ------------------------------------------------------------------

    def _loop(self) -> None:
        while not self._stop_event.is_set():
            with self._anim_lock:
                anim = self._animation

            snap = self._state.snapshot
            color        = self._color()
            muted        = snap["muted"]
            volume_muted = snap["volume_muted"]
            t_total      = snap["timer_total_seconds"]
            t_left       = snap["timer_seconds_left"]
            ha_connected = snap["ha_connected"]
            action_button_down = snap["action_button_down"]

            # Volume Display takes over the ring temporarily, regardless of
            # the current pipeline state, then falls back to the normal
            # animation once its deadline passes.
            if snap["volume_display_until"] > time.monotonic():
                sleep = self._anim_volume_display(color, snap["volume"])

            # The action button forces the ring on at the current (or
            # default) color while physically held down — but only when the
            # HA Light entity is off; if it's already on, the idle animation
            # is already showing the color and there's nothing extra to do.
            # This also carries live updates while the color wheel is being
            # rotated (holding action + a volume button), since that
            # rotation writes the new color straight into shared state.
            elif action_button_down and not snap["light_is_on"]:
                sleep = self._anim_action_held(color, snap["light_brightness"])

            elif anim == self.ANIM_IDLE:
                sleep = self._anim_idle()

            elif anim == self.ANIM_OFF:
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

            if volume_muted:
                self._apply_volume_muted_indicator()

            time.sleep(sleep)


# ===========================================================================
# State → animation mapping
# Mirrors the control_leds script priority logic from ESPHome
# ===========================================================================

def state_to_animation(state: AssistState, ha_connected: bool, muted: bool) -> str:
    if not ha_connected:
        return LEDRing.ANIM_TWINKLE         # Red twinkle = no HA connection

    if state == AssistState.NOT_READY:
        return LEDRing.ANIM_TWINKLE         # Red twinkle

    if state == AssistState.TIMER_RINGING:
        return LEDRing.ANIM_TIMER_RING

    if state == AssistState.WAKE_WORD:
        return LEDRing.ANIM_WAIT_CMD        # Slow clockwise spin

    if state == AssistState.LISTENING:
        return LEDRing.ANIM_LISTENING       # Fast clockwise spin

    if state == AssistState.THINKING:
        return LEDRing.ANIM_THINKING        # Pulsing pair

    if state == AssistState.SPEAKING:
        return LEDRing.ANIM_REPLYING        # Anticlockwise spin

    if state == AssistState.ERROR:
        return LEDRing.ANIM_ERROR           # Red pulse

    if state == AssistState.MUTED or muted:
        return LEDRing.ANIM_MUTED

    if state == AssistState.TIMER_TICKING:
        return LEDRing.ANIM_TIMER_TICK      # Countdown arc

    if state == AssistState.IDLE:
        return LEDRing.ANIM_IDLE            # User color when light on, else off

    # MEDIA_PLAYING, TTS_FINISHED → off
    return LEDRing.ANIM_OFF


# ===========================================================================
# Button handler — reads the HAT buttons from the XMOS XVF3800 over SPI
# ===========================================================================

def parse_gpio_port_a(rx: List[int]) -> Optional[int]:
    """
    Extract the ``gpio_port_a`` byte from an XMOS control response.

    A valid response starts with ``XMOS_RESPONSE_ID`` and does not carry
    ``XMOS_RESPONSE_BUSY`` in its second byte; the 4-byte status register is
    then ``rx[2:6]`` and ``gpio_port_a`` is ``rx[3]``.
    :param rx: Bytes returned by the SPI transfer.
    :return: The gpio_port_a byte, or None if the response is not a valid status.
    """
    if len(rx) < 6 or rx[0] != XMOS_RESPONSE_ID or rx[1] == XMOS_RESPONSE_BUSY:
        return None
    return rx[XMOS_PORT_A_INDEX]


class ButtonHandler:
    """
    Reads the four hardware buttons from the XMOS chip over SPI.

    The buttons are not wired to Raspberry Pi GPIOs. A background thread polls
    the XMOS status register at ~40 Hz and turns changes of its ``gpio_port_a``
    byte into press/release events. Callbacks schedule coroutines onto the
    asyncio event loop thread-safely.

    Supports:
    - Volume +/- for volume control. A short press sends a single step;
      holding the button past VOLUME_HOLD_THRESHOLD_MS repeats that step
      every VOLUME_HOLD_REPEAT_S seconds until released, so volume moves
      gradually while held instead of one step per press.
    - Mute switch for microphone mute. The XMOS latches this switch itself, so
      the handler follows its level: it sends ``mute_mic`` when the switch
      turns on and ``unmute_mic`` when it turns off.
    - Action button for context-aware commands or color changing:
      * Hold + Volume +: Increase hue by 10°
      * Hold + Volume -: Decrease hue by 10°
      Holding the volume button past VOLUME_HOLD_THRESHOLD_MS while the
      action button is held rotates continuously through the color wheel
      using the same repeat mechanism, until the volume button is released.
      Color changes are applied to the LED ring immediately so the wheel
      rotation previews in real time.
    - Action button gestures: double press and triple press (releases less
      than MULTIPRESS_WINDOW_MS apart) and long press (held for at least
      LONG_PRESS_MS). The context command fires on the first press only.
    - The action button also lights up the LED ring at the chosen (or
      default) color for as long as it's physically held down — even for a
      quick tap — turning off again on release. This only happens when the
      HA Light entity is off; if it's already on, the idle animation is
      already showing the color.
    - The "Disable button controls" lock from Home Assistant: while locked,
      no command is sent and the LED ring is not affected. The mute switch
      still latches in the XMOS hardware, which this handler cannot prevent.
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

        # SPI polling
        self._spi = None
        self._poll_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._candidate: Optional[int] = None  # Last raw reading
        self._stable: Optional[int] = None  # Last reading seen twice in a row

        # Track action button hold state for color changing
        self._action_button_held = False
        self._action_active = False  # Action press that was not ignored (lock)
        self._action_pressed_at = 0.0
        self._action_color_used = False
        self._action_hold_timer: Optional[threading.Timer] = None

        # Action button multi-press tracking
        self._gesture_lock = threading.Lock()
        self._tap_count = 0
        self._tap_timer: Optional[threading.Timer] = None

        # Continuous hold-repeat state for the volume buttons, keyed by
        # "up" / "down". `_volume_hold_timers` fires once the button has
        # been held past VOLUME_HOLD_THRESHOLD_MS to kick off the repeat
        # loop; `_volume_repeat_stop` / `_volume_repeat_threads` drive that
        # loop until the button is released.
        self._volume_hold_timers: Dict[str, Optional[threading.Timer]] = {"up": None, "down": None}
        self._volume_repeat_stop: Dict[str, Optional[threading.Event]] = {"up": None, "down": None}
        self._volume_repeat_threads: Dict[str, Optional[threading.Thread]] = {"up": None, "down": None}

    def setup(self) -> None:
        """Open the SPI device and start polling. Never raises: without SPI the
        buttons are disabled but the LED ring keeps working."""
        if not _HAS_SPI:
            _LOGGER.warning("spidev unavailable – buttons not configured")
            return

        try:
            spi = spidev.SpiDev()
            spi.open(SPI_BUS, SPI_DEVICE)
            spi.mode = SPI_MODE
            spi.max_speed_hz = SPI_SPEED_HZ
        except Exception as exc:  # pylint: disable=broad-except
            _LOGGER.error(
                "Cannot open /dev/spidev%d.%d (%s) – buttons disabled. "
                "Enable SPI (dtparam=spi=on) and make sure the device is available "
                "to the container.",
                SPI_BUS, SPI_DEVICE, exc,
            )
            return

        self._spi = spi
        self._stop_event.clear()
        self._poll_thread = threading.Thread(
            target=self._poll_loop, daemon=True, name="button-poll"
        )
        self._poll_thread.start()
        _LOGGER.info(
            "Buttons configured (XMOS over SPI /dev/spidev%d.%d, mode %d, %d Hz)",
            SPI_BUS, SPI_DEVICE, SPI_MODE, SPI_SPEED_HZ,
        )

    def cleanup(self) -> None:
        self._stop_event.set()
        if self._poll_thread is not None and self._poll_thread.is_alive():
            self._poll_thread.join(timeout=1)
        self._poll_thread = None
        if self._spi is not None:
            try:
                self._spi.close()
            except Exception:  # pylint: disable=broad-except
                pass
            self._spi = None
        for which in ("up", "down"):
            self._cancel_volume_hold_timer(which)
            self._stop_volume_repeat(which)
        self._cancel_action_hold_timer()
        with self._gesture_lock:
            if self._tap_timer is not None:
                self._tap_timer.cancel()
                self._tap_timer = None
            self._tap_count = 0

    # ------------------------------------------------------------------
    # SPI polling
    # ------------------------------------------------------------------

    def _read_port(self) -> Optional[int]:
        """Send a NO-OP control frame and return gpio_port_a (None if invalid)."""
        rx = self._spi.xfer2(list(XMOS_NOOP_FRAME))
        return parse_gpio_port_a(rx)

    def _poll_loop(self) -> None:
        failures = 0
        invalid = 0
        while not self._stop_event.is_set():
            try:
                port = self._read_port()
            except Exception as exc:  # pylint: disable=broad-except
                failures += 1
                if failures == 1 or failures % 400 == 0:
                    _LOGGER.warning("SPI read failed (%s), retrying", exc)
                self._stop_event.wait(min(1.0, BTN_POLL_INTERVAL_S * failures))
                continue

            if failures:
                _LOGGER.info("SPI reads recovered after %d failures", failures)
                failures = 0

            if port is None:
                invalid += 1
                if invalid == 1 or invalid % 400 == 0:
                    _LOGGER.debug("XMOS returned no status register (%d times)", invalid)
            else:
                invalid = 0
                try:
                    self._process_port(port)
                except Exception:  # pylint: disable=broad-except
                    _LOGGER.exception("Error handling button state 0x%02x", port)

            self._stop_event.wait(BTN_POLL_INTERVAL_S)

    def _process_port(self, port: int) -> None:
        """
        Debounce a gpio_port_a reading and dispatch any change.

        A new value only counts once it has been read twice in a row.
        """
        if port != self._candidate:
            self._candidate = port
            return
        if port == self._stable:
            return
        previous = self._stable
        self._stable = port
        self._dispatch(port, previous)

    def _dispatch(self, port: int, previous: Optional[int]) -> None:
        """
        Turn a change of gpio_port_a into button events.

        :param port: New debounced gpio_port_a value.
        :param previous: Previous value, or None for the first valid reading.
        """
        _LOGGER.debug("gpio_port_a 0x%02x → 0x%02x", previous if previous is not None else 0xFF, port)

        if previous is None:
            # First reading: adopt the current levels without inventing presses
            # for buttons that are already held. The mute switch is a latch,
            # so a mic left muted must be reported to LVA.
            if port & (1 << BTN_BIT_MUTE):
                self._on_mute_level(True)
            return

        changed = port ^ previous

        # Action first, so a simultaneous action + volume press enters color mode
        if changed & (1 << BTN_BIT_ACTION):
            if port & (1 << BTN_BIT_ACTION):
                self._on_action_button_released()
            else:
                self._on_action_pressed()

        for which, bit in (("up", BTN_BIT_VOLUME_UP), ("down", BTN_BIT_VOLUME_DOWN)):
            if changed & (1 << bit):
                if port & (1 << bit):
                    self._on_volume_button_released(which)
                else:
                    self._on_volume_button_pressed(which)

        if changed & (1 << BTN_BIT_MUTE):
            self._on_mute_level(bool(port & (1 << BTN_BIT_MUTE)))

    # ------------------------------------------------------------------
    # Commands
    # ------------------------------------------------------------------

    def _send(self, command: str) -> None:
        if self._state.snapshot["buttons_locked"]:
            _LOGGER.debug("Button controls locked — ignoring %s", command)
            return
        _LOGGER.info("Button → %s", command)
        asyncio.run_coroutine_threadsafe(
            self._queue.put(command), self._loop
        )

    def _on_volume_up(self) -> None:
        if self._action_button_held:
            self._on_color_hue_increase()
        else:
            self._send("volume_up")

    def _on_volume_down(self) -> None:
        if self._action_button_held:
            self._on_color_hue_decrease()
        else:
            self._send("volume_down")

    def _on_volume_button_pressed(self, which: str) -> None:
        """
        Handle a Volume Up/Down press: fire the immediate single step (volume,
        or hue when the action button is held for color changing), then arm a
        timer that starts continuous repeat if the button is still held past
        VOLUME_HOLD_THRESHOLD_MS.
        """
        if which == "up":
            self._on_volume_up()
        else:
            self._on_volume_down()

        self._cancel_volume_hold_timer(which)
        timer = threading.Timer(
            VOLUME_HOLD_THRESHOLD_MS / 1000.0, self._start_volume_repeat, args=(which,)
        )
        timer.daemon = True
        self._volume_hold_timers[which] = timer
        timer.start()

    def _on_volume_button_released(self, which: str) -> None:
        """Stop any pending hold detection and any active continuous repeat."""
        self._cancel_volume_hold_timer(which)
        self._stop_volume_repeat(which)

    def _cancel_volume_hold_timer(self, which: str) -> None:
        timer = self._volume_hold_timers.get(which)
        if timer is not None:
            timer.cancel()
            self._volume_hold_timers[which] = None

    def _start_volume_repeat(self, which: str) -> None:
        """
        Begin repeating the volume/hue step every VOLUME_HOLD_REPEAT_S while
        the button stays held. Runs in its own daemon thread so the polling
        thread is never blocked; stopped via a threading.Event set from
        `_on_volume_button_released`.
        """
        self._stop_volume_repeat(which)  # Safety: never overlap two loops

        stop_event = threading.Event()
        self._volume_repeat_stop[which] = stop_event

        def _repeat_loop() -> None:
            while not stop_event.wait(VOLUME_HOLD_REPEAT_S):
                if which == "up":
                    self._on_volume_up()
                else:
                    self._on_volume_down()

        thread = threading.Thread(
            target=_repeat_loop, daemon=True, name=f"volume-hold-repeat-{which}"
        )
        self._volume_repeat_threads[which] = thread
        thread.start()

    def _stop_volume_repeat(self, which: str) -> None:
        stop_event = self._volume_repeat_stop.get(which)
        if stop_event is not None:
            stop_event.set()

        thread = self._volume_repeat_threads.get(which)
        if (
            thread is not None
            and thread.is_alive()
            and thread is not threading.current_thread()
        ):
            thread.join(timeout=0.5)

        self._volume_repeat_stop[which] = None
        self._volume_repeat_threads[which] = None

    def _on_action_pressed(self) -> None:
        """
        Action button physically pressed down.

        Fires the context-sensitive command immediately — but only for the
        first press of a multi-press sequence — and marks the button as held
        down so the LED ring can react instantly, even for a very short tap.
        The ring itself only lights up in response to this when the HA Light
        entity is off (see LEDRing._anim_action_held); if it's already on, the
        idle animation is already showing the color and there's nothing extra
        to do. Ignored entirely while button controls are locked.
        """
        if self._state.snapshot["buttons_locked"]:
            _LOGGER.debug("Button controls locked — ignoring action button")
            return

        self._action_active = True
        self._action_pressed_at = time.monotonic()
        self._action_color_used = False
        self._state.update(action_button_down=True)

        self._cancel_action_hold_timer()
        timer = threading.Timer(ACTION_HOLD_MS / 1000.0, self._on_action_held)
        timer.daemon = True
        self._action_hold_timer = timer
        timer.start()

        with self._gesture_lock:
            first_press = self._tap_count == 0
        if first_press:
            self._on_action()

    def _on_action_button_released(self) -> None:
        """
        Action button released – exit color-change mode, stop forcing the ring
        on, and classify the press as a long press or count it as a tap for
        double/triple press detection.
        """
        self._cancel_action_hold_timer()
        if not self._action_active:
            return
        self._action_active = False

        held_ms = (time.monotonic() - self._action_pressed_at) * 1000.0
        color_used = self._action_color_used

        self._on_action_released()
        self._state.update(action_button_down=False)

        if color_used:
            # Color wheel use is not a gesture
            self._reset_taps()
        elif held_ms >= LONG_PRESS_MS:
            self._reset_taps()
            self._send("button_long_press")
        else:
            self._register_tap()

    def _cancel_action_hold_timer(self) -> None:
        timer = self._action_hold_timer
        if timer is not None:
            timer.cancel()
            self._action_hold_timer = None

    def _reset_taps(self) -> None:
        with self._gesture_lock:
            if self._tap_timer is not None:
                self._tap_timer.cancel()
                self._tap_timer = None
            self._tap_count = 0

    def _register_tap(self) -> None:
        """Count a quick action press; the sequence ends MULTIPRESS_WINDOW_MS after the last release."""
        with self._gesture_lock:
            self._tap_count += 1
            if self._tap_timer is not None:
                self._tap_timer.cancel()
            timer = threading.Timer(MULTIPRESS_WINDOW_MS / 1000.0, self._flush_taps)
            timer.daemon = True
            self._tap_timer = timer
            timer.start()

    def _flush_taps(self) -> None:
        with self._gesture_lock:
            count = self._tap_count
            self._tap_count = 0
            self._tap_timer = None
        if count == 2:
            self._send("button_double_press")
        elif count >= 3:
            self._send("button_triple_press")

    def _on_action_held(self) -> None:
        """Action button held – enter color change mode."""
        self._action_button_held = True
        _LOGGER.info("Color change mode active (hold action + use volume buttons)")

    def _on_action_released(self) -> None:
        """Action button released – exit color change mode."""
        self._action_button_held = False

    def _on_color_hue_increase(self) -> None:
        """Increase hue by 10° (volume up while action held)."""
        snap = self._state.snapshot
        new_hue = (snap["hsv_hue"] + HUE_INCREMENT) % 360
        self._send_color_command(new_hue, snap["hsv_saturation"], snap["hsv_value"])

    def _on_color_hue_decrease(self) -> None:
        """Decrease hue by 10° (volume down while action held)."""
        snap = self._state.snapshot
        new_hue = (snap["hsv_hue"] - HUE_INCREMENT) % 360
        self._send_color_command(new_hue, snap["hsv_saturation"], snap["hsv_value"])

    def _send_color_command(self, hue: int, saturation: float, value: float) -> None:
        """
        Convert HSV to RGB, apply it to shared state immediately for a
        real-time preview on the LED ring, and notify LVA/Home Assistant.
        """
        if self._state.snapshot["buttons_locked"]:
            _LOGGER.debug("Button controls locked — ignoring color change")
            return

        self._action_color_used = True
        r, g, b = hsv_to_rgb(hue, saturation, value)

        # Update local state right away so the ring reflects the new color
        # as it's being picked, without waiting on a light_command
        # round-trip through LVA/Home Assistant. This also turns the light
        # "on" — picking a color is an explicit, deliberate action.
        self._state.update(
            hsv_hue=hue,
            hsv_saturation=saturation,
            hsv_value=value,
            light_is_on=True,
            light_red=r / 255.0,
            light_green=g / 255.0,
            light_blue=b / 255.0,
        )

        # Normalize RGB to 0-1 range
        light_cmd = {
            "command": "light_command",
            "data": {
                "object_id": LIGHT_OBJECT_ID,
                "state": True,
                "brightness": self._state.snapshot["light_brightness"],
                "red": r / 255.0,
                "green": g / 255.0,
                "blue": b / 255.0,
            },
        }

        _LOGGER.info("Color change: Hue=%d° → RGB(%d, %d, %d)", hue, r, g, b)

        # Send command to LVA via command queue
        asyncio.run_coroutine_threadsafe(
            self._queue.put(json.dumps(light_cmd)), self._loop
        )

    def _on_mute_level(self, muted: bool) -> None:
        """
        The XMOS mute switch changed (or was found on at startup).

        The switch latches in hardware, so its level — not a press — decides
        the command: ``mute_mic`` when on, ``unmute_mic`` when off.
        """
        self._send("mute_mic" if muted else "unmute_mic")

    def _on_action(self) -> None:
        """
        Context-sensitive bottom button — mirrors HA Voice PE priority:
          1. Timer ringing           → stop_timer_ringing
          2. Pipeline active         → stop_pipeline
          3. Media playing           → stop_media_player
          4. Idle / anything else    → start_listening
        """
        assist = self._state.assist_state

        if assist == AssistState.TIMER_RINGING:
            self._send("stop_timer_ringing")
        elif assist in (AssistState.WAKE_WORD, AssistState.LISTENING, AssistState.THINKING, AssistState.SPEAKING):
            # stop_pipeline aborts the voice pipeline at any of these phases
            self._send("stop_pipeline")
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
            await ws.send(json.dumps({"command": "register_button_lock"}))
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
            item = await self._queue.get()
            try:
                # Item can be either a string (command) or a pre-formatted JSON dict (light_command)
                if isinstance(item, str):
                    # Try to parse as JSON (for light_command) or wrap as command
                    try:
                        msg = json.loads(item)
                    except json.JSONDecodeError:
                        # Plain string command
                        msg = {"command": item}
                else:
                    msg = item
                
                await ws.send(json.dumps(msg))
            except Exception as exc:  # pylint: disable=broad-except
                _LOGGER.warning("Failed to send item %s: %s", item, exc)
                # Put it back so it's not silently dropped
                self._queue.put_nowait(item)
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
                # Not currently sent by LVA's snapshot payload, but read it
                # defensively in case a future LVA version adds it.
                volume_muted=data.get("volume_muted", False),
                ha_connected=data.get("ha_connected", False),
                buttons_locked=data.get("button_controls_locked", False),
            )
            snap = self._state.snapshot
            anim = state_to_animation(
                snap["assist_state"], snap["ha_connected"], snap["muted"]
            )
            self._leds.set_animation(anim)
            return

        elif event == "button_lock_changed":
            locked = data.get("locked", False)
            self._state.update(buttons_locked=locked)
            _LOGGER.info("Button controls %s", "locked" if locked else "unlocked")
            return
      
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
 
        elif event == "disconnected":
            _LOGGER.warning("Home Assistant disconnected")
            self._state.update(
                ha_connected=False,
                assist_state=AssistState.NOT_READY,
            )

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
            self._state.update(
                volume=data.get("volume", 1.0),
                volume_display_until=time.monotonic() + VOLUME_DISPLAY_SECONDS,
            )

        elif event == "volume_muted":
            # Media player (speaker) mute state, distinct from the
            # microphone `muted` event above. Drives the red indicator on
            # the AUX jack corner LEDs (see AUX_JACK_LED_INDICES) rather
            # than swapping the whole ring animation, since the assist
            # pipeline can still be doing something else at the same time.
            volume_muted = data.get("muted", True)
            self._state.update(volume_muted=volume_muted)
            if volume_muted:
                self._state.update(assist_state=AssistState.VOLUME_MUTED)
            elif self._state.assist_state == AssistState.VOLUME_MUTED:
                self._state.update(assist_state=AssistState.IDLE)

        # --- Zeroconf / connection events ----------------------------------
        elif event == "zeroconf":
            status = data.get("status", "")
            if status == "connected":
                self._state.update(ha_connected=True)
                _LOGGER.info("Home Assistant connected")
            elif status == "getting_started":
                _LOGGER.info("LVA starting up, waiting for HA …")

        # --- Light command events ------------------------------------------
        elif event == "light_command":
            # LVA broadcasts to every connected peripheral; only act on
            # commands targeting our registered Light. The Light exposes a
            # single "Voice Assistant" effect, so there is no effect to
            # switch on: we apply on/off, brightness, and color and let the
            # pipeline animations run.
            if data.get("object_id") != LIGHT_OBJECT_ID:
                return
            
            r = float(data.get("red", 0.094))
            g = float(data.get("green", 0.733))
            b = float(data.get("blue", 0.949))
            
            # Convert RGB back to HSV and track hue for color change mode
            hue, sat, val = rgb_to_hsv(
                int(r * 255), int(g * 255), int(b * 255)
            )
            
            self._state.update(
                light_is_on=bool(data.get("state", True)),
                light_brightness=float(data.get("brightness", 0.66)),
                light_red=r,
                light_green=g,
                light_blue=b,
                hsv_hue=hue,
                hsv_saturation=sat,
                hsv_value=val,
            )
            # Force re-render animation with new light state
            snap = self._state.snapshot
            anim = state_to_animation(
                snap["assist_state"], snap["ha_connected"], snap["muted"]
            )
            self._leds.set_animation(anim)
            return

        # --- Recompute LED animation after every event ---------------------
        snap = self._state.snapshot
        anim = state_to_animation(
            snap["assist_state"], snap["ha_connected"], snap["muted"]
        )
        self._leds.set_animation(anim)


# ===========================================================================
# Main
# ===========================================================================

async def async_main(host: str, port: int, led_dma: int = LED_DMA) -> None:
    state         = SharedState()
    command_queue: asyncio.Queue = asyncio.Queue()
    loop          = asyncio.get_running_loop()

    leds    = LEDRing(state, dma=led_dma)
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
        "--led-dma", type=int, default=LED_DMA,
        help=(
            "DMA channel used to drive the LED ring "
            f"(default: {LED_DMA}); must not be one the kernel uses"
        ),
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

    asyncio.run(async_main(args.host, args.port, args.led_dma))


if __name__ == "__main__":
    main()
