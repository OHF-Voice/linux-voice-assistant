"""LED controller for hardware variants.

``LEDController`` is the hardware-agnostic base class. It owns the phase
state machine and the animator and drives any ``APA102``-compatible
strip handed to it. Animations mirror the Home Assistant Voice PE LEDs
and are count-aware, so effects scale to any strip length.

Hardware-specific subclasses supply the right SPI bus, device, and LED
count for their board. ``ReSpeaker2MicV2LEDController`` is the first
such subclass.

See ``LEDController._compute_phase`` for the phase priority order.
"""

from __future__ import annotations

import asyncio
import logging
import math
import time
from enum import Enum
from typing import TYPE_CHECKING, Callable, List, Optional, Tuple, Union

from aioesphomeapi.model import MediaPlayerState

if TYPE_CHECKING:
    from .entity import LEDLightEntity
    from .models import ServerState

_LOGGER = logging.getLogger(__name__)

DEFAULT_BRIGHTNESS = 0.6

RGB = Tuple[int, int, int]
ColorSource = Union[RGB, Callable[[], RGB]]

OFF: RGB = (0, 0, 0)
RED: RGB = (255, 0, 0)
BLUE: RGB = (0, 0, 255)
CYAN: RGB = (0, 200, 200)
GREEN: RGB = (0, 200, 50)
YELLOW: RGB = (220, 180, 0)
DIM_RED: RGB = (80, 0, 0)


def _resolve(color: ColorSource) -> RGB:
    return color() if callable(color) else color


class Phase(str, Enum):
    NOT_READY = "not_ready"
    IDLE = "idle"
    WAKE_WORD = "wake_word"
    LISTENING = "listening"
    THINKING = "thinking"
    SPEAKING = "speaking"
    MUTED = "muted"
    TIMER_TICKING = "timer_ticking"
    TIMER_RINGING = "timer_ringing"
    MEDIA_PLAYING = "media_playing"
    OFF = "off"
    MANUAL = "manual"


class APA102:
    """Minimal APA102 LED strip driver over SPI.

    Frame format per LED: 0xE0|brightness5, blue, green, red.
    """

    def __init__(
        self,
        bus: int,
        device: int,
        speed_hz: int,
        count: int,
    ) -> None:
        # Lazy-import spidev so environments without the respeaker_2mic
        # extra can still import this module (only instantiation fails).
        import spidev  # type: ignore[import-untyped]

        self.count = count
        self._pixels: List[RGB] = [OFF] * count
        self._spi = spidev.SpiDev()
        self._spi.open(bus, device)
        self._spi.max_speed_hz = speed_hz
        self._spi.mode = 0b01  # APA102 uses SPI mode 1
        _LOGGER.info("APA102 SPI driver opened (/dev/spidev%d.%d)", bus, device)

    def set(self, index: int, color: RGB) -> None:
        self._pixels[index % self.count] = color

    def set_all(self, color: RGB) -> None:
        self._pixels = [color] * self.count

    def show(self, brightness: float = DEFAULT_BRIGHTNESS) -> None:
        bright5 = max(0, min(31, int(brightness * 31)))
        frame: List[int] = [0x00, 0x00, 0x00, 0x00]  # start frame
        for r, g, b in self._pixels:
            br = max(0, min(255, int(r * brightness)))
            bg = max(0, min(255, int(g * brightness)))
            bb = max(0, min(255, int(b * brightness)))
            frame += [0xE0 | bright5, bb, bg, br]
        frame += [0xFF] * math.ceil(self.count / 2)  # end frame
        self._spi.xfer2(frame)

    def off(self) -> None:
        self._pixels = [OFF] * self.count
        self.show(0)

    def close(self) -> None:
        self.off()
        self._spi.close()


class LEDAnimator:
    """Runs one asyncio task per phase, cancelling the previous on switch."""

    def __init__(
        self,
        leds: APA102,
        loop: asyncio.AbstractEventLoop,
        user_color: Callable[[], RGB] = lambda: BLUE,
        user_brightness: Callable[[], float] = lambda: 1.0,
    ) -> None:
        self._leds = leds
        self._loop = loop
        self._task: Optional[asyncio.Task] = None
        self._phase: Optional[Phase] = None
        self._timer_total: float = 1.0
        self._timer_ends_at: float = 0.0
        self._user_color = user_color
        self._user_brightness = user_brightness

    def set_timer_progress(self, total_seconds: float, seconds_left: float) -> None:
        self._timer_total = max(1.0, float(total_seconds))
        self._timer_ends_at = time.monotonic() + max(0.0, float(seconds_left))

    def set_phase(self, phase: Phase) -> None:
        """Thread-safe: schedule a phase switch on the event loop."""
        self._loop.call_soon_threadsafe(self._apply_phase, phase)

    def _apply_phase(self, phase: Phase) -> None:
        if phase == self._phase:
            return
        self._phase = phase
        self._cancel()
        _LOGGER.debug("LED phase -> %s", phase.value)
        coro = self._pick_coroutine(phase)
        if coro is not None:
            self._task = self._loop.create_task(coro)

    def _pick_coroutine(self, phase: Phase):
        if phase == Phase.OFF:
            return self._off()
        if phase == Phase.MANUAL:
            return self._manual()
        if phase == Phase.IDLE:
            return self._idle_glow()
        if phase == Phase.NOT_READY:
            return self._pulse_all(DIM_RED)
        if phase == Phase.WAKE_WORD:
            # Wake flash uses the HA-configured color so users can recolor it.
            return self._flash_all(self._user_color, flashes=2, on_ms=120, off_ms=80)
        if phase == Phase.LISTENING:
            return self._chase(self._user_color)
        if phase == Phase.THINKING:
            return self._pulse_all(YELLOW)
        if phase == Phase.SPEAKING:
            return self._breathe_all(GREEN)
        if phase == Phase.MUTED:
            return self._muted()
        if phase == Phase.TIMER_RINGING:
            return self._flash_all(BLUE, on_ms=350, off_ms=250, repeat=True)
        if phase == Phase.TIMER_TICKING:
            return self._timer_tick()
        if phase == Phase.MEDIA_PLAYING:
            return self._steady_all(GREEN, brightness=0.15)
        return self._off()

    def _cancel(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()

    def cleanup(self) -> None:
        self._cancel()
        self._leds.off()

    async def _off(self) -> None:
        self._leds.off()

    async def _manual(self) -> None:
        try:
            while True:
                self._leds.set_all(self._user_color())
                self._leds.show(self._user_brightness())
                await asyncio.sleep(0.2)
        except asyncio.CancelledError:
            self._leds.off()
            raise

    async def _idle_glow(self) -> None:
        try:
            while True:
                self._leds.set_all(self._user_color())
                self._leds.show(0.1 * self._user_brightness())
                await asyncio.sleep(0.25)
        except asyncio.CancelledError:
            self._leds.off()
            raise

    async def _steady_all(
        self, color: ColorSource, brightness: float = DEFAULT_BRIGHTNESS
    ) -> None:
        self._leds.set_all(_resolve(color))
        self._leds.show(brightness)

    async def _muted(self) -> None:
        # First and last LED red, interior off — scales cleanly from 1 LED
        # (single red) to N (bookends red, middle off).
        self._leds.set_all(OFF)
        self._leds.set(0, RED)
        self._leds.set(self._leds.count - 1, RED)
        self._leds.show(0.6)

    async def _flash_all(
        self,
        color: ColorSource,
        flashes: int = 2,
        on_ms: int = 150,
        off_ms: int = 100,
        then_off: bool = False,
        repeat: bool = False,
    ) -> None:
        try:
            count = 0
            while True:
                self._leds.set_all(_resolve(color))
                self._leds.show(1.0)
                await asyncio.sleep(on_ms / 1000)
                self._leds.off()
                await asyncio.sleep(off_ms / 1000)
                count += 1
                if not repeat and count >= flashes:
                    break
            if then_off:
                self._leds.off()
        except asyncio.CancelledError:
            self._leds.off()
            raise

    async def _chase(self, color: ColorSource, step_s: float = 0.12) -> None:
        # Bounce sweep: one lit LED at a time, walks from 0 to end and back.
        count = self._leds.count
        sequence = list(range(count)) + list(range(count - 2, 0, -1))
        pos = 0
        try:
            while True:
                self._leds.set_all(OFF)
                self._leds.set(sequence[pos % len(sequence)], _resolve(color))
                self._leds.show(1.0)
                pos += 1
                await asyncio.sleep(step_s)
        except asyncio.CancelledError:
            self._leds.off()
            raise

    async def _pulse_all(self, color: ColorSource, period: float = 1.0) -> None:
        try:
            while True:
                t = time.monotonic()
                brightness = 0.2 + 0.8 * (
                    0.5 + 0.5 * math.sin(2 * math.pi * t / period)
                )
                self._leds.set_all(_resolve(color))
                self._leds.show(brightness)
                await asyncio.sleep(0.03)
        except asyncio.CancelledError:
            self._leds.off()
            raise

    async def _breathe_all(self, color: ColorSource, period: float = 2.0) -> None:
        try:
            while True:
                t = time.monotonic()
                brightness = 0.1 + 0.9 * (
                    0.5 + 0.5 * math.sin(2 * math.pi * t / period)
                )
                self._leds.set_all(_resolve(color))
                self._leds.show(brightness)
                await asyncio.sleep(0.03)
        except asyncio.CancelledError:
            self._leds.off()
            raise

    async def _timer_tick(self) -> None:
        try:
            while True:
                left = max(0.0, self._timer_ends_at - time.monotonic())
                brightness = max(0.05, min(1.0, left / self._timer_total))
                self._leds.set_all(CYAN)
                self._leds.show(brightness)
                await asyncio.sleep(0.5)
        except asyncio.CancelledError:
            self._leds.off()
            raise


class LEDController:
    """Drives an ``APA102``-compatible LED strip from the voice pipeline.

    Hardware-agnostic: subclasses construct the driver with the right SPI
    bus / device / LED count and hand it to ``__init__``.

    Thread-safe: transition hooks may be called from audio and timer
    threads. Phase changes are scheduled onto the asyncio loop that owns
    the animator.
    """

    def __init__(
        self,
        state: "ServerState",
        loop: asyncio.AbstractEventLoop,
        leds: APA102,
    ) -> None:
        self._state = state
        self._leds = leds
        self._light_entity: "Optional[LEDLightEntity]" = None
        self._animator = LEDAnimator(
            self._leds,
            loop,
            user_color=self._user_color,
            user_brightness=self._user_brightness,
        )

        self._pipeline_phase: Optional[Phase] = None
        self._timer_ringing: bool = False
        self._timer_active: bool = False

        # Initial state: assume not connected until auth completes.
        self._animator.set_phase(Phase.NOT_READY)

    def set_light_entity(self, entity: "Optional[LEDLightEntity]") -> None:
        self._light_entity = entity
        self._recompute()

    def on_light_changed(self) -> None:
        # HA changed on/off, brightness, color or effect — re-evaluate phase so
        # the animator repaints with the new settings immediately.
        self._recompute()

    def _user_color(self) -> RGB:
        if self._light_entity is None:
            return BLUE
        return self._light_entity.rgb_255()

    def _user_brightness(self) -> float:
        if self._light_entity is None:
            return 1.0
        return max(0.0, min(1.0, float(self._light_entity.brightness)))

    def on_ha_connected(self, connected: bool) -> None:
        if not connected:
            self._pipeline_phase = None
            self._timer_ringing = False
            self._timer_active = False
        self._recompute()

    def on_mute_changed(self) -> None:
        self._recompute()

    def on_wake_word(self) -> None:
        self._pipeline_phase = Phase.WAKE_WORD
        self._recompute()

    def on_listening(self) -> None:
        self._pipeline_phase = Phase.LISTENING
        self._recompute()

    def on_thinking(self) -> None:
        self._pipeline_phase = Phase.THINKING
        self._recompute()

    def on_speaking(self) -> None:
        self._pipeline_phase = Phase.SPEAKING
        self._recompute()

    def on_pipeline_idle(self) -> None:
        self._pipeline_phase = None
        self._recompute()

    def on_timer_started(self, total_seconds: float, seconds_left: float) -> None:
        self._timer_active = True
        self._animator.set_timer_progress(total_seconds, seconds_left)
        self._recompute()

    def on_timer_updated(self, total_seconds: float, seconds_left: float) -> None:
        self._animator.set_timer_progress(total_seconds, seconds_left)

    def on_timer_cancelled(self) -> None:
        self._timer_active = False
        self._recompute()

    def on_timer_ringing(self) -> None:
        self._timer_ringing = True
        self._timer_active = False
        self._recompute()

    def on_timer_stopped(self) -> None:
        self._timer_ringing = False
        self._timer_active = False
        self._recompute()

    def on_media_changed(self) -> None:
        self._recompute()

    def _recompute(self) -> None:
        self._animator.set_phase(self._compute_phase())

    def _compute_phase(self) -> Phase:
        # HA-driven overrides — the Light entity is the user-facing source of
        # truth once HA is talking to us.
        if self._light_entity is not None:
            if not self._light_entity.is_on:
                return Phase.OFF
            if self._light_entity.effect == "None":
                return Phase.MANUAL

        if not self._state.connected:
            return Phase.NOT_READY
        if self._timer_ringing:
            return Phase.TIMER_RINGING
        if self._pipeline_phase is not None:
            return self._pipeline_phase
        if self._state.muted:
            return Phase.MUTED
        if self._timer_active:
            return Phase.TIMER_TICKING
        if self._is_media_playing():
            return Phase.MEDIA_PLAYING
        return Phase.IDLE

    def _is_media_playing(self) -> bool:
        entity = self._state.media_player_entity
        if entity is None:
            return False
        return entity.state == MediaPlayerState.PLAYING

    def cleanup(self) -> None:
        self._animator.cleanup()
        self._leds.close()


class ReSpeaker2MicV2LEDController(LEDController):
    """LED controller for the ReSpeaker 2-Mics Pi HAT v2.0.

    Drives 3 APA102 RGB LEDs over SPI0 (/dev/spidev0.0): LED 0 above
    MIC_L, LED 1 centre, LED 2 above MIC_R. The base class's animations
    adapt cleanly to this 3-LED strip — bookends light for the muted
    indicator, and the chase bounces L -> centre -> R -> centre.
    """

    SPI_BUS = 0
    SPI_DEVICE = 0
    SPI_SPEED_HZ = 8_000_000
    LED_COUNT = 3

    def __init__(
        self,
        state: "ServerState",
        loop: asyncio.AbstractEventLoop,
    ) -> None:
        leds = APA102(
            bus=self.SPI_BUS,
            device=self.SPI_DEVICE,
            speed_hz=self.SPI_SPEED_HZ,
            count=self.LED_COUNT,
        )
        super().__init__(state, loop, leds)


def create_led_controller(
    variant: Optional[str],
    state: "ServerState",
    loop: asyncio.AbstractEventLoop,
) -> Optional[LEDController]:
    """Instantiate an LED controller for the requested hardware variant."""
    if not variant or variant == "none":
        return None
    if variant == "respeaker_2mic":
        return ReSpeaker2MicV2LEDController(state, loop)
    raise ValueError(f"Unknown LED controller variant: {variant}")
