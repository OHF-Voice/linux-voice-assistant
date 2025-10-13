"""Shared models."""

import json
import logging
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from queue import Queue
from typing import TYPE_CHECKING, Dict, List, Optional, Union

if TYPE_CHECKING:
    from .entity import ESPHomeEntity, MediaPlayerEntity, MuteSwitchEntity
    from .microwakeword import MicroWakeWord
    from .mpv_player import MpvMediaPlayer
    from .openwakeword import OpenWakeWord
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
    config_path: Path

    def load(
        self, libtensorflowlite_c_path: Path
    ) -> "Union[MicroWakeWord, OpenWakeWord]":
        if self.type == WakeWordType.MICRO_WAKE_WORD:
            from .microwakeword import MicroWakeWord

            return MicroWakeWord.from_config(
                config_path=self.config_path,
                libtensorflowlite_c_path=libtensorflowlite_c_path,
            )

        if self.type == WakeWordType.OPEN_WAKE_WORD:
            from .openwakeword import OpenWakeWord

            return OpenWakeWord.from_config(
                config_path=self.config_path,
                libtensorflowlite_c_path=libtensorflowlite_c_path,
            )

        raise ValueError(f"Unexpected wake word type: {self.type}")


@dataclass
class Preferences:
    active_wake_words: List[str] = field(default_factory=list)


@dataclass
class ServerState:
    name: str
    mac_address: str
    audio_queue: "Queue[Optional[bytes]]"
    entities: "List[ESPHomeEntity]"
    available_wake_words: "Dict[str, AvailableWakeWord]"
    wake_words: "Dict[str, Union[MicroWakeWord, OpenWakeWord]]"
    stop_word: "MicroWakeWord"
    music_player: "MpvMediaPlayer"
    tts_player: "MpvMediaPlayer"
    wakeup_sound: str
    timer_finished_sound: str
    preferences: Preferences
    preferences_path: Path
    libtensorflowlite_c_path: Path

    # openWakeWord
    oww_melspectrogram_path: Path
    oww_embedding_path: Path

    media_player_entity: "Optional[MediaPlayerEntity]" = None
    satellite: "Optional[VoiceSatelliteProtocol]" = None
    mute_switch_entity: "Optional[MuteSwitchEntity]" = None
    wake_words_changed: bool = False
    refractory_seconds: float = 2.0
    muted: bool = False
    connected: bool = False

    def save_preferences(self) -> None:
        """Save preferences as JSON."""
        _LOGGER.debug("Saving preferences: %s", self.preferences_path)
        self.preferences_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.preferences_path, "w", encoding="utf-8") as preferences_file:
            json.dump(
                asdict(self.preferences), preferences_file, ensure_ascii=False, indent=4
            )
