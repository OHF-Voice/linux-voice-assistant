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

    from .entity import ESPHomeEntity, MediaPlayerEntity, TextAttributeEntity
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
    """Per-instance preferences (kept minimal by design)."""

    active_wake_words: List[str] = field(default_factory=list)


@dataclass
class GlobalPreferences:
    """Shared settings across all instances."""

    wake_word_friendly_names: Dict[str, str] = field(default_factory=dict)
    ha_base_url: Optional[str] = None
    ha_token: Optional[str] = None
    ha_history_entity: Optional[str] = None


@dataclass
class ServerState:
    name: str
    mac_address: str
    audio_queue: "Queue[Optional[bytes]]"
    entities: "List[ESPHomeEntity]"
    available_wake_words: "Dict[str, AvailableWakeWord]"
    wake_words: "Dict[str, Union[MicroWakeWord, OpenWakeWord]]"
    active_wake_words: Set[str]
    stop_word: "MicroWakeWord"
    music_player: "MpvMediaPlayer"
    tts_player: "MpvMediaPlayer"
    wakeup_sound: str
    timer_finished_sound: str
    preferences: Preferences
    global_preferences: GlobalPreferences
    preferences_path: Path
    global_preferences_path: Path
    download_dir: Path

    media_player_entity: "Optional[MediaPlayerEntity]" = None
    active_tts_entity: "Optional[TextAttributeEntity]" = None
    active_stt_entity: "Optional[TextAttributeEntity]" = None
    active_assistant_entity: "Optional[TextAttributeEntity]" = None
    satellite: "Optional[VoiceSatelliteProtocol]" = None
    wake_words_changed: bool = False
    refractory_seconds: float = 2.0
    screen_management: int = 0
    disable_wakeword_during_tts: bool = False
    software_mute: bool = False
    shared_mute_path: Path = Path("/dev/shm/lvas_system_mute")

    mute_entity: "Optional[ESPHomeEntity]" = None

    def save_preferences(self) -> None:
        """Save per-instance preferences (currently active wake words)."""
        _LOGGER.debug("Saving preferences: %s", self.preferences_path)
        self.preferences_path.parent.mkdir(parents=True, exist_ok=True)
        to_save = {"active_wake_words": self.preferences.active_wake_words}
        with open(self.preferences_path, "w", encoding="utf-8") as preferences_file:
            json.dump(to_save, preferences_file, ensure_ascii=False, indent=4)
