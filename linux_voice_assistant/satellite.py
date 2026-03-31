"""Voice satellite protocol."""

import asyncio
import hashlib
import logging
import posixpath
import shutil
import threading
import time
from collections.abc import Iterable
from typing import Dict, List, Optional, Set, Union
from urllib.parse import urlparse, urlunparse
from urllib.request import urlopen

# pylint: disable=no-name-in-module
from aioesphomeapi.api_pb2 import (  # type: ignore[attr-defined]
    AuthenticationRequest,
    DeviceInfoRequest,
    DeviceInfoResponse,
    ListEntitiesDoneResponse,
    ListEntitiesRequest,
    MediaPlayerCommandRequest,
    SelectCommandRequest,
    SubscribeHomeAssistantStatesRequest,
    SwitchCommandRequest,
    VoiceAssistantAnnounceFinished,
    VoiceAssistantAnnounceRequest,
    VoiceAssistantAudio,
    VoiceAssistantConfigurationRequest,
    VoiceAssistantConfigurationResponse,
    VoiceAssistantEventResponse,
    VoiceAssistantExternalWakeWord,
    VoiceAssistantRequest,
    VoiceAssistantSetConfiguration,
    VoiceAssistantTimerEventResponse,
    VoiceAssistantWakeWord,
)
from aioesphomeapi.core import MESSAGE_TYPE_TO_PROTO
from aioesphomeapi.model import (
    VoiceAssistantEventType,
    VoiceAssistantFeature,
    VoiceAssistantTimerEventType,
)
from google.protobuf import message
from pymicro_wakeword import MicroWakeWord
from pyopen_wakeword import OpenWakeWord

from .api_server import APIServer
from .entity import (
    MediaPlayerEntity,
    MuteSwitchEntity,
    ThinkingSoundEntity,
    WakeWordSensitivityEntity,
)
from .models import AvailableWakeWord, ServerState, WakeWordType
from .util import call_all

_LOGGER = logging.getLogger(__name__)

PROTO_TO_MESSAGE_TYPE = {v: k for k, v in MESSAGE_TYPE_TO_PROTO.items()}

SENSITIVITY_PRESETS = {
    "Slightly sensitive": {"mww": 0.85, "oww": 0.70},
    "Moderately sensitive": {"mww": 0.70, "oww": 0.50},
    "Very sensitive": {"mww": 0.55, "oww": 0.35},
}


class VoiceSatelliteProtocol(APIServer):

    def __init__(self, state: ServerState) -> None:
        super().__init__(state.name)

        self.state = state
        self.state.connected = False

        # Report capabilities appropriately
        if state.output_only:
            _LOGGER.debug("Output only features")
            self.supported_features = VoiceAssistantFeature.API_AUDIO | VoiceAssistantFeature.ANNOUNCE
        else:
            _LOGGER.debug("Voice assistant features")
            self.supported_features = (
                VoiceAssistantFeature.VOICE_ASSISTANT | VoiceAssistantFeature.API_AUDIO | VoiceAssistantFeature.ANNOUNCE | VoiceAssistantFeature.START_CONVERSATION | VoiceAssistantFeature.TIMERS
            )

        existing_media_players = [entity for entity in self.state.entities if isinstance(entity, MediaPlayerEntity)]

        if existing_media_players:
            # Keep the first instance and remove any extras.
            self.state.media_player_entity = existing_media_players[0]
            for extra in existing_media_players[1:]:
                self.state.entities.remove(extra)

        existing_mute_switches = [entity for entity in self.state.entities if isinstance(entity, MuteSwitchEntity)]
        if existing_mute_switches:
            self.state.mute_switch_entity = existing_mute_switches[0]  # type: ignore
            for extra in existing_mute_switches[1:]:  # type: ignore
                self.state.entities.remove(extra)

        if self.state.media_player_entity is None:
            self.state.media_player_entity = MediaPlayerEntity(
                server=self,
                key=len(state.entities),
                name="Media Player",
                object_id="linux_voice_assistant_media_player",
                music_player=state.music_player,
                announce_player=state.tts_player,
                initial_volume=state.volume,
            )
            self.state.entities.append(self.state.media_player_entity)
        elif self.state.media_player_entity not in self.state.entities:
            self.state.entities.append(self.state.media_player_entity)

        self.state.media_player_entity.server = self
        self.state.media_player_entity.volume = state.volume
        self.state.media_player_entity.previous_volume = state.volume

        # Add/update mute switch entity (like ESPHome Voice PE)
        mute_switch = self.state.mute_switch_entity
        if mute_switch is None:
            mute_switch = MuteSwitchEntity(
                server=self,
                key=len(state.entities),
                name="Mute",
                object_id="mute",
                get_muted=lambda: self.state.muted,
                set_muted=self._set_muted,
            )
            self.state.entities.append(mute_switch)
            self.state.mute_switch_entity = mute_switch
        elif mute_switch not in self.state.entities:
            self.state.entities.append(mute_switch)

        mute_switch.server = self
        mute_switch.update_get_muted(lambda: self.state.muted)
        mute_switch.update_set_muted(self._set_muted)
        mute_switch.sync_with_state()

        existing_thinking_sound_switches = [entity for entity in self.state.entities if isinstance(entity, ThinkingSoundEntity)]
        if existing_thinking_sound_switches:
            self.state.thinking_sound_entity = existing_thinking_sound_switches[0]  # type: ignore
            for extra in existing_thinking_sound_switches[1:]:  # type: ignore
                self.state.entities.remove(extra)

        # Add/update thinking sound entity
        thinking_sound_switch = self.state.thinking_sound_entity
        if thinking_sound_switch is None:
            thinking_sound_switch = ThinkingSoundEntity(
                server=self,
                key=len(state.entities),
                name="Thinking Sound",
                object_id="thinking_sound",
                get_thinking_sound_enabled=lambda: self.state.thinking_sound_enabled,
                set_thinking_sound_enabled=self._set_thinking_sound_enabled,
            )
            self.state.entities.append(thinking_sound_switch)
            self.state.thinking_sound_entity = thinking_sound_switch
        elif thinking_sound_switch not in self.state.entities:
            self.state.entities.append(thinking_sound_switch)

        # Load thinking sound enabled state from preferences (default to False if not set or unknown)
        if hasattr(self.state.preferences, "thinking_sound") and self.state.preferences.thinking_sound in (0, 1):
            self.state.thinking_sound_enabled = bool(self.state.preferences.thinking_sound)
        else:
            self.state.thinking_sound_enabled = False

        thinking_sound_switch.server = self
        thinking_sound_switch.update_get_thinking_sound_enabled(lambda: self.state.thinking_sound_enabled)
        thinking_sound_switch.update_set_thinking_sound_enabled(self._set_thinking_sound_enabled)
        thinking_sound_switch.sync_with_state()

        # Add/update wake word sensitivity entity
        self._setup_sensitivity_entity()
        self._apply_sensitivity(self.state.wake_word_sensitivity)

        self._is_streaming_audio = False
        self._tts_url: Optional[str] = None
        self._tts_played = False
        self._continue_conversation = False
        self._timer_finished = False
        self._timer_ring_start: Optional[float] = None
        self._processing = False
        self._pipeline_active = False
        self._external_wake_words: Dict[str, VoiceAssistantExternalWakeWord] = {}
        self._disconnect_event = asyncio.Event()

        # Must be last — prevents race with audio thread
        self.state.satellite = self

    def _setup_sensitivity_entity(self) -> None:
        existing = [entity for entity in self.state.entities if isinstance(entity, WakeWordSensitivityEntity)]
        if existing:
            sensitivity_entity = existing[0]
            for extra in existing[1:]:
                self.state.entities.remove(extra)
        else:
            sensitivity_entity = WakeWordSensitivityEntity(
                server=self,
                key=len(self.state.entities),
                name="Wake word sensitivity",
                object_id="wake_word_sensitivity",
                get_sensitivity=lambda: self.state.wake_word_sensitivity,
                set_sensitivity=self._set_sensitivity,
            )
            self.state.entities.append(sensitivity_entity)

        sensitivity_entity.server = self
        sensitivity_entity.update_get_sensitivity(lambda: self.state.wake_word_sensitivity)
        sensitivity_entity.update_set_sensitivity(self._set_sensitivity)
        sensitivity_entity.sync_with_state()
        self.state.sensitivity_entity = sensitivity_entity

    def _set_sensitivity(self, level: str) -> None:
        if level not in SENSITIVITY_PRESETS:
            _LOGGER.warning("Unknown sensitivity level: %s", level)
            return
        self.state.wake_word_sensitivity = level
        self.state.preferences.wake_word_sensitivity = level
        self.state.save_preferences()
        self._apply_sensitivity(level)
        _LOGGER.info("Wake word sensitivity set to: %s", level)

    def _apply_sensitivity(self, level: str) -> None:
        preset = SENSITIVITY_PRESETS.get(level, SENSITIVITY_PRESETS["Slightly sensitive"])
        mww_cutoff = preset["mww"]
        oww_cutoff = preset["oww"]

        for ww in self.state.wake_words.values():
            if isinstance(ww, MicroWakeWord):
                try:
                    ww.probability_cutoff = mww_cutoff
                    _LOGGER.debug("MWW cutoff set to %.2f for %s", mww_cutoff, ww.id)
                except Exception:  # pylint: disable=broad-except
                    _LOGGER.exception("Failed to set MWW cutoff for %s", ww.id)

        self.state.oww_probability_cutoff = oww_cutoff
        _LOGGER.debug("OWW threshold set to %.2f", oww_cutoff)

    def _set_thinking_sound_enabled(self, new_state: bool) -> None:
        self.state.thinking_sound_enabled = bool(new_state)
        self.state.preferences.thinking_sound = 1 if self.state.thinking_sound_enabled else 0

        if self.state.thinking_sound_enabled:
            _LOGGER.debug("Thinking sound enabled")
        else:
            _LOGGER.debug("Thinking sound disabled")
            pass
        self.state.save_preferences()

    def _set_muted(self, new_state: bool) -> None:
        self.state.muted = bool(new_state)

        if self.state.muted:
            # voice_assistant.stop behavior
            _LOGGER.debug("Muting voice assistant (voice_assistant.stop)")
            self._is_streaming_audio = False
            self.state.tts_player.stop()
            # Stop any ongoing voice processing
            self.state.stop_word.is_active = False  # type: ignore
            self.state.tts_player.play(self.state.mute_sound)
        else:
            # voice_assistant.start_continuous behavior
            _LOGGER.debug("Unmuting voice assistant (voice_assistant.start_continuous)")
            self.state.tts_player.play(self.state.unmute_sound)
            # Resume normal operation - wake word detection will be active again
            pass

    def handle_voice_event(self, event_type: VoiceAssistantEventType, data: Dict[str, str]) -> None:
        _LOGGER.debug("Voice event: type=%s, data=%s", event_type.name, data)

        if event_type == VoiceAssistantEventType.VOICE_ASSISTANT_RUN_START:
            self._tts_url = data.get("url")
            self._tts_played = False
            self._continue_conversation = False
            self._pipeline_active = True
        elif event_type == VoiceAssistantEventType.VOICE_ASSISTANT_INTENT_START and self.state.thinking_sound_enabled:
            # Play short "thinking/processing" sound if configured
            processing = getattr(self.state, "processing_sound", None)
            if processing:
                _LOGGER.debug("Playing processing sound: %s", processing)
                self.state.stop_word.is_active = True  # type: ignore
                self._processing = True
                self.duck()
                self.state.tts_player.play(self.state.processing_sound)
        elif event_type in (
            VoiceAssistantEventType.VOICE_ASSISTANT_STT_VAD_END,
            VoiceAssistantEventType.VOICE_ASSISTANT_STT_END,
        ):
            self._is_streaming_audio = False
        elif event_type == VoiceAssistantEventType.VOICE_ASSISTANT_INTENT_PROGRESS:
            if data.get("tts_start_streaming") == "1":
                # Start streaming early
                self.play_tts()
        elif event_type == VoiceAssistantEventType.VOICE_ASSISTANT_INTENT_END:
            if data.get("continue_conversation") == "1":
                self._continue_conversation = True
        elif event_type == VoiceAssistantEventType.VOICE_ASSISTANT_TTS_END:
            self._tts_url = data.get("url")
            self.play_tts()
        elif event_type == VoiceAssistantEventType.VOICE_ASSISTANT_RUN_END:
            self._is_streaming_audio = False
            if not self._tts_played:
                self._pipeline_active = False
                self._tts_finished()
            # When TTS is playing, keep _pipeline_active = True to block
            # false wake word detections from speaker audio feedback.
            # _tts_finished() callback will clear it when playback ends.

            self._tts_played = False

        # TODO: handle error

    def handle_timer_event(
        self,
        event_type: VoiceAssistantTimerEventType,
        msg: VoiceAssistantTimerEventResponse,
    ) -> None:
        _LOGGER.debug("Timer event: type=%s", event_type.name)
        if event_type == VoiceAssistantTimerEventType.VOICE_ASSISTANT_TIMER_FINISHED:
            if not self._timer_finished:
                self.state.active_wake_words.add(self.state.stop_word.id)
                self._timer_finished = True
                self._timer_ring_start = time.monotonic()
                self.duck()
                self._play_timer_finished()

    def handle_message(self, msg: message.Message) -> Iterable[message.Message]:
        if isinstance(msg, VoiceAssistantEventResponse):
            # Pipeline event
            data: Dict[str, str] = {}
            for arg in msg.data:
                data[arg.name] = arg.value

            self.handle_voice_event(VoiceAssistantEventType(msg.event_type), data)
        # assist_satellite.announce HERE
        elif isinstance(msg, VoiceAssistantAnnounceRequest):
            _LOGGER.debug("Announcing: %s", msg.text)

            assert self.state.media_player_entity is not None

            urls = []
            if msg.preannounce_media_id:
                urls.append(msg.preannounce_media_id)

            urls.append(msg.media_id)

            self.state.active_wake_words.add(self.state.stop_word.id)
            self._continue_conversation = msg.start_conversation

            self.duck()
            self.state.tts_player.play(urls, done_callback=self._tts_finished)
        elif isinstance(msg, VoiceAssistantTimerEventResponse):
            self.handle_timer_event(VoiceAssistantTimerEventType(msg.event_type), msg)
        elif isinstance(msg, DeviceInfoRequest):
            _LOGGER.debug("Device info request")

            yield DeviceInfoResponse(
                uses_password=False,
                name=self.state.name,
                friendly_name=self.state.friendly_name,
                project_name="Open Home Foundation.Linux Voice Assistant",
                project_version=self.state.version,
                esphome_version=self.state.esphome_version,
                mac_address=self.state.mac_address,
                manufacturer="Open Home Foundation",
                model="Linux Voice Assistant",
                voice_assistant_feature_flags=self.supported_features,
            )
        elif isinstance(
            msg,
            (
                ListEntitiesRequest,
                SubscribeHomeAssistantStatesRequest,
                MediaPlayerCommandRequest,
                SwitchCommandRequest,
                SelectCommandRequest,
            ),
        ):
            for entity in self.state.entities:
                yield from entity.handle_message(msg)

            if isinstance(msg, ListEntitiesRequest):
                yield ListEntitiesDoneResponse()
        elif isinstance(msg, VoiceAssistantConfigurationRequest):
            available_wake_words = [
                VoiceAssistantWakeWord(
                    id=ww.id,
                    wake_word=ww.wake_word,
                    trained_languages=ww.trained_languages,
                )
                for ww in self.state.available_wake_words.values()
            ]

            for eww in msg.external_wake_words:
                if eww.model_type != "micro":
                    continue

                available_wake_words.append(
                    VoiceAssistantWakeWord(
                        id=eww.id,
                        wake_word=eww.wake_word,
                        trained_languages=eww.trained_languages,
                    )
                )

                self._external_wake_words[eww.id] = eww

            yield VoiceAssistantConfigurationResponse(
                available_wake_words=available_wake_words,
                active_wake_words=[ww.id for ww in self.state.wake_words.values() if ww.id in self.state.active_wake_words],
                max_active_wake_words=2,
            )
            _LOGGER.info("Connected to Home Assistant")
        elif isinstance(msg, VoiceAssistantSetConfiguration):
            # Change active wake words
            active_wake_words: Set[str] = set()

            for wake_word_id in msg.active_wake_words:
                if wake_word_id in self.state.wake_words:
                    # Already active
                    active_wake_words.add(wake_word_id)
                    continue

                model_info = self.state.available_wake_words.get(wake_word_id)
                if not model_info:
                    # Check external wake words (may require download)
                    external_wake_word = self._external_wake_words.get(wake_word_id)
                    if not external_wake_word:
                        continue

                    model_info = self._download_external_wake_word(external_wake_word)
                    if not model_info:
                        continue

                    self.state.available_wake_words[wake_word_id] = model_info

                _LOGGER.debug("Loading wake word: %s", model_info.wake_word_path)
                self.state.wake_words[wake_word_id] = model_info.load()

                _LOGGER.info("Wake word set: %s", wake_word_id)
                active_wake_words.add(wake_word_id)
                break

            self.state.active_wake_words = active_wake_words
            _LOGGER.debug("Active wake words: %s", active_wake_words)

            self.state.preferences.active_wake_words = list(active_wake_words)
            self.state.save_preferences()
            self.state.wake_words_changed = True

    def handle_audio(self, audio_chunk: bytes) -> None:

        if not self._is_streaming_audio or self.state.muted:
            return

        self.send_messages([VoiceAssistantAudio(data=audio_chunk)])

    def wakeup(self, wake_word: Union[MicroWakeWord, OpenWakeWord]) -> None:
        if self._timer_finished:
            # Stop the ringing timer, then start a normal wake-up after a short
            # delay so the transition doesn't feel abrupt.
            self._timer_finished = False
            self._timer_ring_start = None
            self.state.active_wake_words.discard(self.state.stop_word.id)
            self.unduck()
            self.state.tts_player.stop()
            _LOGGER.debug("Stopping timer finished sound; will wake up in 1 s")

            wake_word_phrase = wake_word.wake_word  # type: ignore

            def _delayed_wakeup() -> None:
                if self.state.muted or self._pipeline_active:
                    _LOGGER.debug("Delayed wakeup skipped (muted=%s, pipeline_active=%s)", self.state.muted, self._pipeline_active)
                    return
                _LOGGER.debug("Delayed wakeup: playing wakeup sound for %s", wake_word_phrase)
                self._pipeline_active = True
                self.duck()
                self.state.tts_player.play(
                    self.state.wakeup_sound,
                    done_callback=lambda: self._on_wakeup_sound_finished(wake_word_phrase),
                )

            threading.Timer(0.1, _delayed_wakeup).start()
            return

        if self.state.muted:
            # Don't respond to wake words when muted (voice_assistant.stop behavior)
            return

        if self._pipeline_active:
            _LOGGER.debug("Ignoring wake word - pipeline already active")
            return

        wake_word_phrase = wake_word.wake_word  # type: ignore
        _LOGGER.debug("Detected wake word: %s", wake_word_phrase)
        self._pipeline_active = True
        self.duck()
        self.state.tts_player.play(
            self.state.wakeup_sound,
            done_callback=lambda: self._on_wakeup_sound_finished(wake_word_phrase),
        )

    def _on_wakeup_sound_finished(self, wake_word_phrase: str) -> None:
        """Callback invoked when the wakeup sound finishes playing."""
        _LOGGER.debug(
            "Wakeup sound finished, starting audio streaming with wake word: %s",
            wake_word_phrase,
        )
        self.send_messages(
            [VoiceAssistantRequest(start=True, wake_word_phrase=wake_word_phrase)],
        )
        self._is_streaming_audio = True

    def stop(self) -> None:
        self.state.active_wake_words.discard(self.state.stop_word.id)
        self._pipeline_active = False

        if self._timer_finished:
            self._timer_finished = False
            self._timer_ring_start = None
            self.unduck()
            self.state.tts_player.stop()
            _LOGGER.debug("Stopping timer finished sound")
        else:
            # tts_player.stop() invokes the done_callback (_tts_finished),
            # so we don't call _tts_finished() again explicitly.
            self.state.tts_player.stop()
            _LOGGER.debug("TTS response stopped manually")

    def play_tts(self) -> None:
        if (not self._tts_url) or self._tts_played:
            return

        self._tts_played = True
        _LOGGER.debug("Playing TTS response: %s", self._tts_url)

        self.state.active_wake_words.add(self.state.stop_word.id)
        self.state.tts_player.play(self._tts_url, done_callback=self._tts_finished)

    def duck(self) -> None:
        _LOGGER.debug("Ducking music")
        self.state.music_player.duck()

    def unduck(self) -> None:
        _LOGGER.debug("Unducking music")
        self.state.music_player.unduck()

    def _tts_finished(self) -> None:
        self._pipeline_active = False
        self.state.active_wake_words.discard(self.state.stop_word.id)
        self.send_messages([VoiceAssistantAnnounceFinished()])

        if self._continue_conversation:
            self.send_messages([VoiceAssistantRequest(start=True)])
            self._is_streaming_audio = True
            self._pipeline_active = True
            _LOGGER.debug("Continuing conversation")
        else:
            self.unduck()

        _LOGGER.debug("TTS response finished")

    def _play_timer_finished(self) -> None:
        if not self._timer_finished:
            _LOGGER.debug("Timer finished sound stopped")
            self.unduck()
            self._timer_ring_start = None
            return

        # Auto-stop after timer_max_ring_seconds
        if self._timer_ring_start is not None:
            elapsed = time.monotonic() - self._timer_ring_start
            if elapsed >= self.state.timer_max_ring_seconds:
                _LOGGER.info(
                    "Timer auto-stopped after %.0f seconds (max=%.0f)",
                    elapsed,
                    self.state.timer_max_ring_seconds,
                )
                self._timer_finished = False
                self._timer_ring_start = None
                self.state.active_wake_words.discard(self.state.stop_word.id)
                self.unduck()
                return

        self.state.tts_player.play(
            self.state.timer_finished_sound,
            done_callback=lambda: call_all(
                lambda: time.sleep(1.0),
                self._play_timer_finished,
            ),
        )

    def connection_lost(self, exc):
        super().connection_lost(exc)

        self._disconnect_event.set()
        self._is_streaming_audio = False
        self._tts_url = None
        self._tts_played = False
        self._continue_conversation = False
        self._timer_finished = False
        self._pipeline_active = False

        # Stop any ongoing audio playback and wake/stop word processing.
        try:
            self.state.music_player.stop()
        except Exception:  # pragma: no cover - defensive safety net
            _LOGGER.exception("Failed to stop music player during disconnect")

        try:
            self.state.tts_player.stop()
        except Exception:  # pragma: no cover - defensive safety net
            _LOGGER.exception("Failed to stop TTS player during disconnect")

        self.state.stop_word.is_active = False
        self.state.connected = False
        if self.state.satellite is self:
            self.state.satellite = None

        if self.state.mute_switch_entity is not None:
            self.state.mute_switch_entity.sync_with_state()

        _LOGGER.info("Disconnected from Home Assistant; waiting for reconnection")

    def process_packet(self, msg_type: int, packet_data: bytes) -> None:
        super().process_packet(msg_type, packet_data)

        if msg_type == PROTO_TO_MESSAGE_TYPE[AuthenticationRequest]:
            self.state.connected = True
            _LOGGER.debug("Authentication successful, connected to Home Assistant")
            # Send states after connect
            states: List[message.Message] = []
            _LOGGER.debug("Found %d entities in state", len(self.state.entities))
            for i, entity in enumerate(self.state.entities):
                entity_states = list(entity.handle_message(SubscribeHomeAssistantStatesRequest()))
                states.extend(entity_states)
                _LOGGER.debug(
                    "Entity %d (%s) returned %d state messages",
                    i,
                    type(entity).__name__,
                    len(entity_states),
                )
            _LOGGER.debug("Total state messages to send: %d", len(states))
            self.send_messages(states)
            for i, msg in enumerate(states):
                _LOGGER.debug("Sent state message %d: %s", i, type(msg).__name__)
            _LOGGER.debug("All entity states sent after connect")

    def _download_external_wake_word(self, external_wake_word: VoiceAssistantExternalWakeWord) -> Optional[AvailableWakeWord]:
        eww_dir = self.state.download_dir / "external_wake_words"
        eww_dir.mkdir(parents=True, exist_ok=True)

        config_path = eww_dir / f"{external_wake_word.id}.json"
        should_download_config = not config_path.exists()

        # Check if we need to download the model file
        model_path = eww_dir / f"{external_wake_word.id}.tflite"
        should_download_model = True
        if model_path.exists():
            model_size = model_path.stat().st_size
            if model_size == external_wake_word.model_size:
                with open(model_path, "rb") as model_file:
                    model_hash = hashlib.sha256(model_file.read()).hexdigest()

                if model_hash == external_wake_word.model_hash:
                    should_download_model = False
                    _LOGGER.debug(
                        "Model size and hash match for %s. Skipping download.",
                        external_wake_word.id,
                    )

        if should_download_config or should_download_model:
            # Download config
            _LOGGER.debug("Downloading %s to %s", external_wake_word.url, config_path)
            with urlopen(external_wake_word.url) as request:
                if request.status != 200:
                    _LOGGER.warning(
                        "Failed to download: %s, status=%s",
                        external_wake_word.url,
                        request.status,
                    )
                    return None

                with open(config_path, "wb") as model_file:
                    shutil.copyfileobj(request, model_file)

        if should_download_model:
            # Download model file
            parsed_url = urlparse(external_wake_word.url)
            parsed_url = parsed_url._replace(
                path=posixpath.join(posixpath.dirname(parsed_url.path), model_path.name),
            )
            model_url = urlunparse(parsed_url)

            _LOGGER.debug("Downloading %s to %s", model_url, model_path)
            with urlopen(model_url) as request:
                if request.status != 200:
                    _LOGGER.warning("Failed to download: %s, status=%s", model_url, request.status)
                    return None

                with open(model_path, "wb") as model_file:
                    shutil.copyfileobj(request, model_file)

        return AvailableWakeWord(
            id=external_wake_word.id,
            type=WakeWordType.MICRO_WAKE_WORD,
            wake_word=external_wake_word.wake_word,
            trained_languages=external_wake_word.trained_languages,
            wake_word_path=config_path,
        )
