"""Physical button handler for hardware variants.

``ButtonController`` is the hardware-agnostic base class. It owns the
click-counting state machine, press sounds, HA event dispatch, and mute
toggle. A short press toggles mute through the same path as the HA Mute
switch (keeping the mute sound, HA state, and LEDs in sync); single /
double / triple / long press patterns also fire on the Button Event
entity for HA automations.

Hardware-specific subclasses wire a physical button to ``_on_released``
and ``_on_held``. ``ReSpeaker2MicV2ButtonController`` is the first such
subclass.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Dict, Optional

from aioesphomeapi.api_pb2 import SwitchCommandRequest  # type: ignore[attr-defined]

if TYPE_CHECKING:
    from gpiozero import Button  # type: ignore[import-untyped]

    from .entity import ButtonEventEntity
    from .models import ServerState

_LOGGER = logging.getLogger(__name__)

# Clicks arriving within this window after a release are grouped into
# a single double/triple press. 400 ms matches HAVPE's multi-click feel.
MULTI_CLICK_WINDOW = 0.4
# Long-press threshold; fires after the button has been held this long.
LONG_PRESS_HOLD_TIME = 0.6

# Sounds that play on multi/long press — single press keeps the existing
# mute-switch sound as its cue, matching HAVPE's feedback pattern.
_PRESS_SOUND_FILES: Dict[str, str] = {
    "double_press": "center_button_double_press.flac",
    "triple_press": "center_button_triple_press.flac",
    "long_press": "center_button_long_press.flac",
}


class ButtonController:
    """Multi-click / long-press state machine for a single tactile button.

    Hardware-agnostic: subclasses own a physical button and call
    ``_on_released`` / ``_on_held`` from its callbacks (on any thread —
    the base hops onto the asyncio loop before touching shared state).
    The base class decides single / double / triple / long press from
    those signals, plays the matching press sound, fires the HA event,
    and (for single press) toggles mute through the Mute switch entity.
    """

    def __init__(
        self,
        state: "ServerState",
        loop: asyncio.AbstractEventLoop,
    ) -> None:
        self._state = state
        self._loop = loop
        self._event_entity: "Optional[ButtonEventEntity]" = None
        self._click_count = 0
        self._finalize_handle: Optional[asyncio.TimerHandle] = None
        self._long_press_fired = False
        # Sounds live alongside the mute sound — which is always configured —
        # so we don't need a separate CLI flag for each press variant.
        sounds_dir = Path(self._state.mute_sound).parent
        self._press_sounds: Dict[str, str] = {
            event: str(sounds_dir / filename)
            for event, filename in _PRESS_SOUND_FILES.items()
        }

    def set_event_entity(self, entity: "Optional[ButtonEventEntity]") -> None:
        self._event_entity = entity

    def _on_released(self) -> None:
        # Hardware callbacks may arrive on a non-asyncio thread; hop onto
        # the loop before touching shared state or timers.
        self._loop.call_soon_threadsafe(self._handle_released)

    def _on_held(self) -> None:
        self._loop.call_soon_threadsafe(self._handle_held)

    def _handle_held(self) -> None:
        # Long press takes over — cancel any pending click finalization
        # and suppress the release that will arrive after the hold.
        self._long_press_fired = True
        self._click_count = 0
        if self._finalize_handle is not None:
            self._finalize_handle.cancel()
            self._finalize_handle = None
        _LOGGER.info("Button -> long_press")
        self._play_press_sound("long_press")
        self._fire_event("long_press")

    def _handle_released(self) -> None:
        if self._long_press_fired:
            # Swallow the release that follows a long press.
            self._long_press_fired = False
            return

        self._click_count += 1
        if self._finalize_handle is not None:
            self._finalize_handle.cancel()
        self._finalize_handle = self._loop.call_later(
            MULTI_CLICK_WINDOW, self._finalize_clicks
        )

    def _finalize_clicks(self) -> None:
        count = self._click_count
        self._click_count = 0
        self._finalize_handle = None

        if count == 1:
            # Single press keeps HAVPE's default action: toggle mute.
            self._toggle_mute()
            _LOGGER.info("Button -> single_press")
            self._fire_event("single_press")
        elif count == 2:
            _LOGGER.info("Button -> double_press")
            self._play_press_sound("double_press")
            self._fire_event("double_press")
        elif count >= 3:
            _LOGGER.info("Button -> triple_press")
            self._play_press_sound("triple_press")
            self._fire_event("triple_press")

    def _fire_event(self, event_type: str) -> None:
        if self._event_entity is not None:
            self._event_entity.fire(event_type)

    def _play_press_sound(self, event_type: str) -> None:
        sound = self._press_sounds.get(event_type)
        if sound is None:
            return
        self._state.tts_player.play(sound)

    def _toggle_mute(self) -> None:
        # Dispatch through the Mute switch entity so the button takes
        # exactly the same path HA does when the switch is toggled.
        satellite = self._state.satellite
        mute_switch = self._state.mute_switch_entity
        if satellite is None or mute_switch is None:
            _LOGGER.debug("Button pressed but mute switch is unavailable")
            return

        new_state = not self._state.muted
        _LOGGER.info("Button -> toggling mute to %s", new_state)
        satellite.send_messages(
            list(
                mute_switch.handle_message(
                    SwitchCommandRequest(key=mute_switch.key, state=new_state)
                )
            )
        )

    def cleanup(self) -> None:
        if self._finalize_handle is not None:
            self._finalize_handle.cancel()
            self._finalize_handle = None


class ReSpeaker2MicV2ButtonController(ButtonController):
    """Button controller for the ReSpeaker 2-Mics Pi HAT v2.0.

    The HAT exposes a single tactile button on GPIO17 (BCM, physical pin
    11), pulled high so the input reads low while pressed. Uses gpiozero
    to observe press / release / held edges and forwards them to the
    base class's state machine.
    """

    BUTTON_GPIO = 17
    BUTTON_BOUNCE_TIME = 0.05

    def __init__(
        self,
        state: "ServerState",
        loop: asyncio.AbstractEventLoop,
    ) -> None:
        # Lazy-import gpiozero so environments without the respeaker_2mic
        # extra can still import this module (only instantiation fails).
        from gpiozero import Button  # type: ignore[import-untyped]

        super().__init__(state, loop)
        button = Button(
            self.BUTTON_GPIO,
            pull_up=True,
            bounce_time=self.BUTTON_BOUNCE_TIME,
            hold_time=LONG_PRESS_HOLD_TIME,
        )
        button.when_released = self._on_released
        button.when_held = self._on_held
        self._button: Optional["Button"] = button
        _LOGGER.info("Button armed on GPIO%d", self.BUTTON_GPIO)

    def cleanup(self) -> None:
        if self._button is not None:
            self._button.close()
            self._button = None
        super().cleanup()


def create_button_controller(
    variant: Optional[str],
    state: "ServerState",
    loop: asyncio.AbstractEventLoop,
) -> Optional[ButtonController]:
    """Instantiate a button controller for the named hardware variant."""
    if not variant or variant == "none":
        return None
    if variant == "respeaker_2mic":
        return ReSpeaker2MicV2ButtonController(state, loop)
    raise ValueError(f"Unknown button controller variant: {variant}")
