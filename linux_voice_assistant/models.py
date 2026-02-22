"""Shared models."""

import json
import logging
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from queue import Queue
from typing import TYPE_CHECKING, Dict, List, Optional, Set, Union

if TYPE_CHECKING:
    from pymicro_wakeword import MicroWakeWord
    from pyopen_wakeword import OpenWakeWord

    from .entity import (
        ESPHomeEntity,
        MediaPlayerEntity,
        MuteSwitchEntity,
        ThinkingSoundEntity,
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
class Preferences:
    active_wake_words: List[str] = field(default_factory=list)
    volume: Optional[float] = None
    thinking_sound: int = 0  # 0 = disabled, 1 = enabled


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
    processing_sound: str
    timer_finished_sound: str
    mute_sound: str
    unmute_sound: str
    preferences: Preferences
    preferences_path: Path
    download_dir: Path

    media_player_entity: "Optional[MediaPlayerEntity]" = None
    satellite: "Optional[VoiceSatelliteProtocol]" = None
    mute_switch_entity: "Optional[MuteSwitchEntity]" = None
    thinking_sound_entity: "Optional[ThinkingSoundEntity]" = None
    wake_words_changed: bool = False
    refractory_seconds: float = 2.0
    thinking_sound_enabled: bool = False
    muted: bool = False
    connected: bool = False
    volume: float = 1.0

    def save_preferences(self) -> None:
        """Save preferences as JSON."""
        _LOGGER.debug("Saving preferences: %s", self.preferences_path)
        self.preferences_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.preferences_path, "w", encoding="utf-8") as preferences_file:
            json.dump(
                asdict(self.preferences), preferences_file, ensure_ascii=False, indent=4
            )

    def persist_volume(self, volume: float) -> None:
        """Persist the normalized media volume (0.0 - 1.0)."""
        clamped_volume = max(0.0, min(1.0, volume))
        _LOGGER.debug(f"persist_volume called: new={clamped_volume}, current={self.volume}, prefs={self.preferences.volume}")

        if (
            abs(self.volume - clamped_volume) < 0.0001
            and self.preferences.volume is not None
            and abs(self.preferences.volume - clamped_volume) < 0.0001
        ):
            _LOGGER.debug("Skipping save - volume unchanged")
            return

        self.volume = clamped_volume
        self.preferences.volume = clamped_volume
        _LOGGER.info(f"Saving volume {clamped_volume} to {self.preferences_path}")
        self.save_preferences()
        _LOGGER.info(f"Volume saved successfully")
