"""Shared models."""

import json
import logging
import time
from collections.abc import Iterable
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from queue import Queue
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Set, Union

from .util import call_all

if TYPE_CHECKING:
    from google.protobuf import message
    from pymicro_wakeword import MicroWakeWord
    from pyopen_wakeword import OpenWakeWord

    from .entity import (
        ButtonEventSensorEntity,
        ButtonLockEntity,
        ESPHomeEntity,
        LEDLightEntity,
        MediaPlayerEntity,
        MicSettingEntity,
        MuteSwitchEntity,
        StopWordSensitivityNumberEntity,
        ThinkingSoundEntity,
        WakeWord1SensitivityNumberEntity,
        WakeWord2SensitivityNumberEntity,
    )
    from .mpv_player import MpvMediaPlayer
    from .satellite import VoiceSatelliteProtocol

_LOGGER = logging.getLogger(__name__)


class WakeWordType(str, Enum):
    MICRO_WAKE_WORD = "micro"
    OPEN_WAKE_WORD = "openWakeWord"


@dataclass
class AvailableWakeWord:
    id: str
    type: WakeWordType
    wake_word: str
    trained_languages: List[str]
    wake_word_path: Path
    probability_cutoff: float = 0.7

    def load(self) -> "Union[MicroWakeWord, OpenWakeWord]":
        if self.type == WakeWordType.MICRO_WAKE_WORD:
            from pymicro_wakeword import MicroWakeWord

            return MicroWakeWord.from_config(config_path=self.wake_word_path)

        if self.type == WakeWordType.OPEN_WAKE_WORD:
            from pyopen_wakeword import OpenWakeWord

            oww_model = OpenWakeWord.from_model(model_path=self.wake_word_path)
            setattr(oww_model, "wake_word", self.wake_word)

            return oww_model

        raise ValueError(f"Unexpected wake word type: {self.type}")


@dataclass
class LightRegistration:
    """Capabilities a peripheral declares for one of its Light entities.

    The peripheral sends this with the register_light command after
    connecting. LVA materialises a matching LEDLightEntity so HA can
    control it.
    """

    name: str
    object_id: str
    icon: str = "mdi:led-strip-variant"
    effects: List[str] = field(default_factory=list)
    supports_rgb: bool = True
    supports_brightness: bool = True


@dataclass
class LocalTimer:
    """
    Local countdown tracker for a Home Assistant timer.

    Populated from VOICE_ASSISTANT_TIMER_STARTED/UPDATED events so LVA can
    keep counting down (and ring) locally even if the connection to Home
    Assistant is lost before the timer actually finishes.
    """

    id: str
    name: str
    total_seconds: int
    ends_at: float  # time.monotonic() deadline

    def seconds_left(self, now: Optional[float] = None) -> int:
        """Return the remaining whole seconds, clamped to zero."""
        now = time.monotonic() if now is None else now
        return max(0, int(round(self.ends_at - now)))

    def is_expired(self, now: Optional[float] = None) -> bool:
        """Return whether the local deadline has passed."""
        now = time.monotonic() if now is None else now
        return now >= self.ends_at


@dataclass
class Preferences:
    active_wake_words: List[Optional[str]] = field(default_factory=list)
    volume: Optional[float] = None
    thinking_sound: int = 0  # 0 = disabled, 1 = enabled
    button_controls_locked: int = 0  # 0 = buttons enabled (default), 1 = buttons locked
    wake_word_1_sensitivity: Optional[float] = None
    wake_word_2_sensitivity: Optional[float] = None
    stop_word_sensitivity: Optional[float] = None

    mic_auto_gain: int = 0
    mic_noise_suppression: int = 0
    mic_volume: int = 100  # 1–100, default maximum


@dataclass
class ServerState:
    name: str
    friendly_name: str
    mac_address: str
    ip_address: str
    network_interface: str
    version: str
    esphome_version: str
    audio_queue: "Queue[Optional[bytes]]"
    entities: "List[ESPHomeEntity]"
    available_wake_words: "Dict[str, AvailableWakeWord]"
    wake_words: "Dict[str, Union[MicroWakeWord, OpenWakeWord]]"
    active_wake_words: Set[str]
    stop_word: "MicroWakeWord"
    music_player: "MpvMediaPlayer"
    tts_player: "MpvMediaPlayer"
    wakeup_sound: str
    start_listening_sound: str
    processing_sound: str
    timer_finished_sound: str
    mute_sound: str
    unmute_sound: str
    button_double_press_sound: str
    button_triple_press_sound: str
    button_long_press_sound: str
    preferences: Preferences
    preferences_path: Path
    download_dir: Path
    continue_conversation_delay: float = 0.5  # seconds to wait after TTS before opening mic

    media_player_entity: "Optional[MediaPlayerEntity]" = None
    satellite: "Optional[VoiceSatelliteProtocol]" = None
    connections: "List[VoiceSatelliteProtocol]" = field(default_factory=list)
    mute_switch_entity: "Optional[MuteSwitchEntity]" = None
    thinking_sound_entity: "Optional[ThinkingSoundEntity]" = None
    button_event_sensor_entity: "Optional[ButtonEventSensorEntity]" = None

    # Lights declared by peripherals via register_light. Survives HA
    # reconnects so the satellite can rebuild its entities whenever it
    # is constructed again.
    pending_lights: "List[LightRegistration]" = field(default_factory=list)
    # Materialised LightEntities keyed by object_id, so light_command
    # events can be routed back to the right peripheral hardware.
    led_light_entities: "Dict[str, LEDLightEntity]" = field(default_factory=dict)

    # True once a peripheral sends register_button. Gates creation of
    # ButtonEventSensorEntity so the HA device page only shows the button
    # entity when hardware that actually supports button presses is present.
    # Survives HA reconnects so the entity is re-registered automatically.
    pending_button: bool = False

    # Local countdown state for active timers, keyed by timer id. Lets LVA keep
    # ticking down (and ring) even if the connection to Home Assistant drops
    # mid-timer. Populated/refreshed from VOICE_ASSISTANT_TIMER_STARTED/UPDATED
    # and cleared on VOICE_ASSISTANT_TIMER_CANCELLED/FINISHED.
    local_timers: "Dict[str, LocalTimer]" = field(default_factory=dict)

    # id of the timer currently ringing (if any). Guards against a local
    # expiry and a (possibly late) VOICE_ASSISTANT_TIMER_FINISHED for the
    # same timer both starting the ring loop.
    ringing_timer_id: Optional[str] = None
    timer_ring_start: Optional[float] = None
    # True once a peripheral sends register_button_lock. Gates creation of
    # ButtonLockEntity so the HA device page only shows the "Disable button
    # controls" switch when hardware that actually supports it is present.
    # Survives HA reconnects so the entity is re-registered automatically.
    pending_button_lock: bool = False
    button_lock_entity: "Optional[ButtonLockEntity]" = None

    # Optional peripheral WebSocket API (LEDs, buttons, HAT boards).
    # Assigned in __main__ before the event loop starts.
    peripheral_api: "Optional[Any]" = None  # PeripheralAPIServer at runtime

    sensitivity_1_number_entity: "Optional[WakeWord1SensitivityNumberEntity]" = None
    sensitivity_2_number_entity: "Optional[WakeWord2SensitivityNumberEntity]" = None
    stop_sensitivity_number_entity: "Optional[StopWordSensitivityNumberEntity]" = None
    mic_gain_entity: "Optional[MicSettingEntity]" = None
    mic_noise_suppression_entity: "Optional[MicSettingEntity]" = None
    mic_volume_entity: "Optional[MicSettingEntity]" = None
    wake_words_changed: bool = False
    refractory_seconds: float = 2.0
    thinking_sound_enabled: bool = False
    button_controls_locked: bool = False
    output_only: bool = False
    muted: bool = False
    connected: bool = False
    volume: float = 1.0
    oww_probability_cutoff: float = 0.7  # Dynamic threshold for OpenWakeWord
    oww_second_probability_cutoff: float = 0.7  # Dynamic threshold for second OpenWakeWord
    oww_stop_probability_cutoff: float = 0.5  # Dynamic threshold for Stop word
    wake_word_1_threshold: float = 0.7
    wake_word_2_threshold: float = 0.7
    stop_word_threshold: float = 0.5
    mic_auto_gain: int = 0
    mic_noise_suppression: int = 0
    mic_volume: int = 100  # 1–100, default maximum
    audio_input_channels: int = 2  # number of mic channels to stream
    timer_max_ring_seconds: float = 900.0
    listen_during_wake_sound: bool = False

    def broadcast(self, msgs: "Iterable[message.Message]") -> None:
        """Send messages to every connected API client.

        Entity state changes that happen asynchronously (not in response to a
        request) must reach *all* subscribed clients, not just whichever
        connection happens to be referenced by an entity's ``server``. Without
        this fan-out a second API client leaves Home Assistant stuck on a stale
        state (e.g. ``playing`` after playback has finished).
        """
        messages = list(msgs)
        if not messages:
            return
        for connection in list(self.connections):
            connection.send_messages(messages)

    def save_preferences(self) -> None:
        """Save preferences as JSON."""
        _LOGGER.debug("Saving preferences: %s", self.preferences_path)
        self.preferences_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.preferences_path, "w", encoding="utf-8") as preferences_file:
            json.dump(
                asdict(self.preferences),
                preferences_file,
                ensure_ascii=False,
                indent=4,
            )

    def persist_volume(self, volume: float) -> None:
        """Persist the normalized media volume (0.0 - 1.0)."""
        clamped_volume = max(0.0, min(1.0, volume))
        _LOGGER.debug(
            "persist_volume called: new=%s, current=%s, prefs=%s",
            clamped_volume,
            self.volume,
            self.preferences.volume,
        )

        if abs(self.volume - clamped_volume) < 0.0001 and self.preferences.volume is not None and abs(self.preferences.volume - clamped_volume) < 0.0001:
            _LOGGER.debug("Skipping save - volume unchanged")
            return

        previous_muted = self.volume == 0.0
        self.volume = clamped_volume
        self.preferences.volume = clamped_volume
        _LOGGER.info("Saving volume %s to %s", clamped_volume, self.preferences_path)
        self.save_preferences()
        _LOGGER.info("Volume saved successfully")

        # Notify peripheral container (thread-safe; may be called from mpv callbacks)
        api = self.peripheral_api
        if api is not None:
            from .peripheral_api import LVAEvent  # local import avoids circular dep

            api.emit_event_sync(LVAEvent.VOLUME_CHANGED, {"volume": round(clamped_volume, 3)})

            new_muted = clamped_volume == 0.0
            if previous_muted != new_muted:
                api.emit_event_sync(LVAEvent.VOLUME_MUTED, {"muted": new_muted})

    def persist_mic_gain(self, gain: float) -> None:
        """Persist the microphone auto gain value."""
        gain_int = int(gain)
        if self.mic_auto_gain == gain_int and self.preferences.mic_auto_gain == gain_int:
            return

        self.mic_auto_gain = gain_int
        self.preferences.mic_auto_gain = gain_int
        self.save_preferences()

    def persist_mic_noise(self, noise: float) -> None:
        """Persist the microphone noise suppression value."""
        noise_int = int(noise)
        if self.mic_noise_suppression == noise_int and self.preferences.mic_noise_suppression == noise_int:
            return

        self.mic_noise_suppression = noise_int
        self.preferences.mic_noise_suppression = noise_int
        self.save_preferences()

    def persist_mic_volume(self, volume: float) -> None:
        """Persist the microphone input volume (0–100)."""
        volume_int = max(1, min(100, int(round(volume))))
        if self.mic_volume == volume_int and self.preferences.mic_volume == volume_int:
            return

        self.mic_volume = volume_int
        self.preferences.mic_volume = volume_int
        _LOGGER.info("Saving mic_volume %s to %s", volume_int, self.preferences_path)
        self.save_preferences()

    def update_local_timer(self, timer_id: str, name: str, total_seconds: int, seconds_left: int) -> None:
        """
        Record/refresh the local countdown for a running timer.
        :param timer_id: id of the timer, as reported by Home Assistant.
        :param name: display name of the timer.
        :param total_seconds: original timer duration.
        :param seconds_left: remaining seconds as of this update.
        """
        self.local_timers[timer_id] = LocalTimer(
            id=timer_id,
            name=name,
            total_seconds=total_seconds,
            ends_at=time.monotonic() + max(0, int(seconds_left)),
        )

    def cancel_local_timer(self, timer_id: str) -> None:
        """Drop local countdown tracking for a timer Home Assistant cancelled."""
        self.local_timers.pop(timer_id, None)
        if self.ringing_timer_id == timer_id:
            self.stop_timer_ringing()

    def check_local_timers(self) -> None:
        """
        Poll local timers for local expiry.

        Called periodically from the main event loop so a timer still rings
        even if Home Assistant (and therefore VOICE_ASSISTANT_TIMER_FINISHED)
        is unreachable when the countdown reaches zero.
        """
        if not self.local_timers:
            return

        now = time.monotonic()
        expired = [timer for timer in self.local_timers.values() if timer.is_expired(now)]
        for timer in expired:
            timer_data = {
                "id": timer.id,
                "name": timer.name,
                "total_seconds": timer.total_seconds,
                "seconds_left": 0,
            }
            self.start_timer_ringing(timer.id, timer_data)

    def start_timer_ringing(self, timer_id: str, timer_data: Dict[str, Any]) -> None:
        """
        Start the timer-finished ring loop.

        Safe to call from either the HA VOICE_ASSISTANT_TIMER_FINISHED handler
        or the local countdown watchdog; a no-op if a timer is already ringing
        so the two triggers can't double-ring.
        :param timer_id: id of the timer that finished.
        :param timer_data: event payload (id/name/total_seconds/seconds_left) forwarded to peripherals.
        """
        if self.ringing_timer_id is not None:
            return

        _LOGGER.info("Timer '%s' finished, starting ring", timer_id)
        self.ringing_timer_id = timer_id
        self.timer_ring_start = time.monotonic()
        self.local_timers.pop(timer_id, None)
        self.active_wake_words.add(self.stop_word.id)
        self.music_player.duck()

        self._emit_timer_event("timer_ringing", timer_data)
        self._continue_timer_ring()

    def _continue_timer_ring(self) -> None:
        """Loop the timer-finished sound until stopped or the max ring duration elapses."""
        if self.ringing_timer_id is None:
            return

        if self.timer_ring_start is not None:
            elapsed = time.monotonic() - self.timer_ring_start
            if elapsed >= self.timer_max_ring_seconds:
                _LOGGER.info(
                    "Timer auto-stopped after %.0f seconds (max=%.0f)",
                    elapsed,
                    self.timer_max_ring_seconds,
                )
                self.stop_timer_ringing()
                return

        self.tts_player.play(
            self.timer_finished_sound,
            done_callback=lambda: call_all(lambda: time.sleep(1.0), self._continue_timer_ring),
        )

    def stop_timer_ringing(self) -> None:
        """Stop a currently-ringing timer, if any."""
        if self.ringing_timer_id is None:
            return

        _LOGGER.debug("Stopping timer ring for '%s'", self.ringing_timer_id)
        self.ringing_timer_id = None
        self.timer_ring_start = None
        self.active_wake_words.discard(self.stop_word.id)
        self.music_player.unduck()
        self.tts_player.stop()
        self._emit_timer_event("idle", None)

    def _emit_timer_event(self, event: str, data: Optional[Dict[str, Any]]) -> None:
        """Emit a peripheral event by name, tolerating a missing/disabled peripheral API."""
        api = self.peripheral_api
        if api is None:
            return
        from .peripheral_api import LVAEvent  # local import avoids a circular dependency

        api.emit_event_sync(LVAEvent(event), data)


def initial_stop_word_threshold(saved_sensitivity: Optional[float]) -> float:
    """
    Resolve the stop word probability cutoff to start from, clamped to 0.0-1.0.
    :param saved_sensitivity: Value persisted in preferences, or None if it has never been set.
    """
    if saved_sensitivity is None:
        return ServerState.stop_word_threshold

    return max(0.0, min(1.0, float(saved_sensitivity)))
