"""Voice satellite protocol."""

import asyncio
import functools
import hashlib
import logging
import posixpath
import shutil
import time
from collections.abc import Iterable
from pathlib import Path
from typing import Dict, Optional, Set, Union, List
from urllib.parse import urlparse, urlunparse
from urllib.request import urlopen

from aioesphomeapi.api_pb2 import (
    DeviceInfoRequest,
    DeviceInfoResponse,
    ListEntitiesDoneResponse,
    ListEntitiesRequest,
    MediaPlayerCommandRequest,
    SubscribeHomeAssistantStatesRequest,
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
from aioesphomeapi.model import (
    VoiceAssistantEventType,
    VoiceAssistantFeature,
    VoiceAssistantTimerEventType,
)
from google.protobuf import message
from pymicro_wakeword import MicroWakeWord
from pyopen_wakeword import OpenWakeWord

from .api_server import APIServer
from .entity import MediaPlayerEntity
from .error_handling import retry_with_backoff, NetworkError
from .models import AvailableWakeWord, ServerState, SatelliteState, WakeWordType
from .util import call_all

_LOGGER = logging.getLogger(__name__)


class VoiceSatelliteProtocol(APIServer):
    """
    ESPHome voice assistant satellite protocol handler.

    Manages the complete voice assistant lifecycle including:
    - Wake word configuration and loading
    - Voice assistant request/response flow
    - TTS playback and announcements
    - Timer alarms with configurable auto-stop
    - Media player entity for Home Assistant integration

    Implements the ESPHome API protocol for voice assistant communication.
    """

    def __init__(self, state: ServerState) -> None:
        """
        Initialize the voice satellite protocol.

        Args:
            state: Shared server state with configuration and resources
        """
        super().__init__(state.name)
        self.state = state
        self.state.satellite = self

        # --- Ensure exactly one MediaPlayerEntity with a stable key ---
        if self.state.entities:
            existing = self.state.entities[0]
            if not isinstance(existing, MediaPlayerEntity):
                _LOGGER.warning(
                    "First ESPHome entity is not MediaPlayerEntity (%r). "
                    "Replacing it with a new MediaPlayerEntity.",
                    type(existing),
                )
                self.media_player_entity = MediaPlayerEntity(
                    server=self,
                    state=state,
                    key=0,
                    name="Media Player",
                    object_id="linux_voice_assistant_media_player",
                    music_player=state.music_player,
                    announce_player=state.tts_player,
                )
                self.state.entities[0] = self.media_player_entity
            else:
                # Reuse existing entity but bind it to the current protocol
                self.media_player_entity = existing
                self.media_player_entity.server = self
                self.media_player_entity.key = 0
        else:
            # First connection in this process: create the media player once
            self.media_player_entity = MediaPlayerEntity(
                server=self,
                state=state,
                key=0,
                name="Media Player",
                object_id="linux_voice_assistant_media_player",
                music_player=state.music_player,
                announce_player=state.tts_player,
            )
            self.state.entities.append(self.media_player_entity)

        # If more entities somehow accumulated, prune them to avoid confusing HA
        if len(self.state.entities) > 1:
            _LOGGER.warning(
                "Pruning %d extra ESPHome entities; keeping only the first.",
                len(self.state.entities) - 1,
            )
            del self.state.entities[1:]

        # State machine
        self._state: SatelliteState = SatelliteState.STARTING
        self._is_streaming_audio: bool = False

        self._tts_url: Optional[str] = None
        self._continue_conversation: bool = False
        self._timer_finished: bool = False

        self._run_end_received: bool = False
        self._tts_end_received: bool = False
        # Track if current audio is an announcement
        self._is_announcement: bool = False

        # External wake words announced by Home Assistant
        self._external_wake_words: Dict[str, VoiceAssistantExternalWakeWord] = {}

        # Timer alarm auto-stop handle
        self._timer_auto_stop_handle: Optional[asyncio.TimerHandle] = None

        # CRITICAL FIX: Lock for thread-safe timer operations
        import threading
        self._timer_lock = threading.Lock()

    # -------------------------------------------------------------------------
    # State machine helpers
    # -------------------------------------------------------------------------

    def _set_state(self, new_state: SatelliteState):
        if self._state == new_state:
            return

        _LOGGER.debug("State transition: %s -> %s", self._state, new_state)
        self._state = new_state

        if new_state == SatelliteState.IDLE:
            self.unduck()
            self.state.active_wake_words.discard(self.state.stop_word.id)
            self.state.event_bus.publish("voice_idle")
        elif new_state == SatelliteState.LISTENING:
            self.duck()
            self.state.event_bus.publish("voice_listen")
        elif new_state == SatelliteState.THINKING:
            self.state.event_bus.publish("voice_thinking")
        elif new_state == SatelliteState.RESPONDING:
            self.state.active_wake_words.add(self.state.stop_word.id)
            self.state.event_bus.publish("voice_responding")
        elif new_state == SatelliteState.ERROR:
            self.state.event_bus.publish("voice_error")

    # -------------------------------------------------------------------------
    # Voice assistant events
    # -------------------------------------------------------------------------

    def handle_voice_event(
        self, event_type: VoiceAssistantEventType, data: Dict[str, str]
    ) -> None:
        _LOGGER.debug("Voice event: type=%s, data=%s", event_type.name, data)

        if event_type == VoiceAssistantEventType.VOICE_ASSISTANT_RUN_START:
            self._run_end_received = False
            self._tts_end_received = False
            self._is_announcement = False
            self._tts_url = data.get("url")
            self._continue_conversation = False

        elif event_type == VoiceAssistantEventType.VOICE_ASSISTANT_STT_START:
            self._set_state(SatelliteState.LISTENING)

        elif event_type == VoiceAssistantEventType.VOICE_ASSISTANT_STT_VAD_START:
            self.state.event_bus.publish("voice_vad_start", data)

        elif event_type == VoiceAssistantEventType.VOICE_ASSISTANT_STT_END:
            self._is_streaming_audio = False
            self._set_state(SatelliteState.THINKING)

        elif event_type == VoiceAssistantEventType.VOICE_ASSISTANT_STT_VAD_END:
            # No-op for now; could be used for LED cues
            pass

        elif event_type == VoiceAssistantEventType.VOICE_ASSISTANT_INTENT_END:
            if data.get("continue_conversation") == "1":
                self._continue_conversation = True

        elif event_type == VoiceAssistantEventType.VOICE_ASSISTANT_TTS_START:
            self._set_state(SatelliteState.RESPONDING)

        elif event_type == VoiceAssistantEventType.VOICE_ASSISTANT_TTS_END:
            self._tts_url = data.get("url")
            self._tts_end_received = True
            self.play_tts()

        elif event_type == VoiceAssistantEventType.VOICE_ASSISTANT_RUN_END:
            self._run_end_received = True
            if self._state != SatelliteState.RESPONDING:
                self._determine_final_state()

        elif event_type == VoiceAssistantEventType.VOICE_ASSISTANT_ERROR:
            code = data.get("code")
            message = data.get("message")
            _LOGGER.debug(
                "VoiceAssistant error received: code=%s, message=%s", code, message
            )

            # Treat "no text recognized" as a benign outcome, not a hard error.
            if code == "stt-no-text-recognized":
                _LOGGER.debug(
                    "No text recognized from STT; treating as benign and returning to IDLE."
                )
                # Ensure we stop streaming audio for this run
                self._is_streaming_audio = False
                # Go directly back to IDLE (unduck, idle LEDs, etc.)
                self._set_state(SatelliteState.IDLE)
                return

            # All other errors follow the normal error path.
            self._set_state(SatelliteState.ERROR)
            # After a brief period, return to IDLE automatically.
            self.state.loop.call_later(5.0, self._set_state, SatelliteState.IDLE)

    def handle_timer_event(
        self,
        event_type: VoiceAssistantTimerEventType,
        msg: VoiceAssistantTimerEventResponse,
    ) -> None:
        _LOGGER.debug("Timer event: type=%s", event_type.name)
        if event_type == VoiceAssistantTimerEventType.VOICE_ASSISTANT_TIMER_FINISHED:
            # CRITICAL FIX: Protect timer state with lock
            with self._timer_lock:
                if not self._timer_finished:
                    self.state.active_wake_words.add(self.state.stop_word.id)
                    self._timer_finished = True

                    # Schedule auto-stop if configured
                    duration = getattr(
                        self.state.preferences, "alarm_duration_seconds", 0
                    )
                    if duration > 0:
                        if self._timer_auto_stop_handle is not None:
                            self._timer_auto_stop_handle.cancel()
                        _LOGGER.debug(
                            "Scheduling auto-stop for timer alarm after %s seconds",
                            duration,
                        )
                        self._timer_auto_stop_handle = self.state.loop.call_later(
                            duration, self._auto_stop_timer_alarm
                        )

            # Audio operations outside lock
            self.duck()
            self._play_timer_finished()

    # -------------------------------------------------------------------------
    # Main message handler (called by APIServer)
    # -------------------------------------------------------------------------

    def handle_message(self, msg: message.Message) -> Iterable[message.Message]:
        """
        Handles incoming messages from Home Assistant.
        Note: This method is synchronous. Long-running operations must be offloaded to Tasks.
        """
        if isinstance(msg, VoiceAssistantEventResponse):
            data: Dict[str, str] = {}
            for arg in msg.data:
                data[arg.name] = arg.value
            self.handle_voice_event(VoiceAssistantEventType(msg.event_type), data)

        elif isinstance(msg, VoiceAssistantAnnounceRequest):
            _LOGGER.debug("Announcing: %s", msg.text)
            urls: List[str] = []
            if msg.preannounce_media_id:
                urls.append(msg.preannounce_media_id)
            urls.append(msg.media_id)

            self._is_announcement = True
            self.state.active_wake_words.add(self.state.stop_word.id)
            self._continue_conversation = msg.start_conversation
            self.duck()
            self._set_state(SatelliteState.RESPONDING)
            yield from self.media_player_entity.play(
                urls, announcement=True, done_callback=self._tts_finished
            )

        elif isinstance(msg, VoiceAssistantTimerEventResponse):
            self.handle_timer_event(VoiceAssistantTimerEventType(msg.event_type), msg)

        elif isinstance(msg, DeviceInfoRequest):
            yield DeviceInfoResponse(
                uses_password=False,
                name=self.state.name,
                mac_address=self.state.mac_address,
                voice_assistant_feature_flags=(
                    VoiceAssistantFeature.VOICE_ASSISTANT
                    | VoiceAssistantFeature.API_AUDIO
                    | VoiceAssistantFeature.ANNOUNCE
                    | VoiceAssistantFeature.START_CONVERSATION
                    | VoiceAssistantFeature.TIMERS
                ),
            )

        elif isinstance(
            msg,
            (
                ListEntitiesRequest,
                SubscribeHomeAssistantStatesRequest,
                MediaPlayerCommandRequest,
            ),
        ):
            for entity in self.state.entities:
                yield from entity.handle_message(msg)

            if isinstance(msg, ListEntitiesRequest):
                yield ListEntitiesDoneResponse()

        elif isinstance(msg, VoiceAssistantConfigurationRequest):
            # Build list of available wake words (built-in + external)
            available_wake_words: List[VoiceAssistantWakeWord] = [
                VoiceAssistantWakeWord(
                    id=ww.id,
                    wake_word=ww.wake_word,
                    trained_languages=ww.trained_languages,
                )
                for ww in self.state.available_wake_words.values()
            ]

            # Reset external wake words cache and add new ones
            self._external_wake_words.clear()
            for eww in msg.external_wake_words:
                if eww.model_type != "micro":
                    _LOGGER.warning(
                        "Skipping external wake word %s (type=%s)",
                        eww.id,
                        eww.model_type,
                    )
                    continue

                available_wake_words.append(
                    VoiceAssistantWakeWord(
                        id=eww.id,
                        wake_word=eww.wake_word,
                        trained_languages=eww.trained_languages,
                    )
                )
                self._external_wake_words[eww.id] = eww

            _LOGGER.debug(
                "VoiceAssistantConfigurationRequest: external_wake_words=%s",
                [eww.id for eww in msg.external_wake_words],
            )

            # IMPORTANT: Use self.state.active_wake_words directly here, instead of
            # filtering through self.state.wake_words (which may not yet contain
            # newly requested models while they're still loading).
            yield VoiceAssistantConfigurationResponse(
                available_wake_words=available_wake_words,
                active_wake_words=sorted(self.state.active_wake_words),
                max_active_wake_words=2,
            )

            _LOGGER.info("Connected to Home Assistant")
            self._set_state(SatelliteState.IDLE)
            self.state.event_bus.publish("ha_connected")

        elif isinstance(msg, VoiceAssistantSetConfiguration):
            requested_ids = list(msg.active_wake_words)
            _LOGGER.debug(
                "VoiceAssistantSetConfiguration received: active_wake_words=%s",
                requested_ids,
            )

            # Update the active_wake_words set immediately so that the *next*
            # VoiceAssistantConfigurationRequest sees the new state, even while
            # we are still downloading/loading models in the background.
            self.state.active_wake_words = set(requested_ids)

            # Offload heavy work (downloads + model loading) to a background task
            self.state.loop.create_task(self._handle_set_configuration_task(msg))
            # Yield nothing immediately; response to ConfigurationRequest is handled
            # separately in the VoiceAssistantConfigurationRequest branch.

    # -------------------------------------------------------------------------
    # SetConfiguration handler (async, runs in background)
    # -------------------------------------------------------------------------

    async def _handle_set_configuration_task(
        self, msg: VoiceAssistantSetConfiguration
    ) -> None:
        """Asynchronous handler for SetConfiguration to avoid blocking I/O."""
        requested_ids = list(msg.active_wake_words)
        _LOGGER.debug(
            "Applying SetConfiguration: requested active_wake_words=%s",
            requested_ids,
        )

        active_wake_words: Set[str] = set()

        for wake_word_id in requested_ids:
            if wake_word_id in self.state.wake_words:
                # Already loaded in this process; just mark it active.
                active_wake_words.add(wake_word_id)
                continue

            model_info = self.state.available_wake_words.get(wake_word_id)

            if not model_info:
                external_wake_word = self._external_wake_words.get(wake_word_id)
                if not external_wake_word:
                    _LOGGER.warning("Unknown wake word: %s", wake_word_id)
                    continue

                # Await the non-blocking download
                model_info = await self._download_external_wake_word(
                    external_wake_word
                )
                if not model_info:
                    continue

                self.state.available_wake_words[wake_word_id] = model_info

            _LOGGER.debug("Loading wake word: %s", model_info.wake_word_path)
            # CRITICAL FIX: Protect wake_words dict modification with lock
            with self.state.wake_words_lock:
                self.state.wake_words[wake_word_id] = model_info.load()

            _LOGGER.info("Wake word set: %s", wake_word_id)
            active_wake_words.add(wake_word_id)
            # Do NOT break; we want to process all requested wake words.

        # Finalize active wake words with the subset that actually succeeded
        # CRITICAL FIX: Protect active_wake_words update with lock
        with self.state.wake_words_lock:
            self.state.active_wake_words = active_wake_words
            self.state.wake_words_changed = True

        _LOGGER.debug(
            "Active wake words after SetConfiguration: %s", active_wake_words
        )

        self.state.preferences.active_wake_words = list(active_wake_words)
        self.state.save_preferences()

    # -------------------------------------------------------------------------
    # Audio handling and wake word triggers
    # -------------------------------------------------------------------------

    def handle_audio(self, audio_chunk: bytes) -> None:
        if self._is_streaming_audio:
            self.send_messages([VoiceAssistantAudio(data=audio_chunk)])

    def _clear_timer_auto_stop(self) -> None:
        """Cancel any pending auto-stop for the timer alarm. Thread-safe."""
        # CRITICAL FIX: Protect timer handle access with lock
        with self._timer_lock:
            if self._timer_auto_stop_handle is not None:
                try:
                    self._timer_auto_stop_handle.cancel()
                except (RuntimeError, asyncio.CancelledError) as e:
                    _LOGGER.warning("Timer auto-stop handle already cancelled or invalid: %s", e)
                except Exception as e:
                    _LOGGER.error("Unexpected error cancelling timer auto-stop: %s", e, exc_info=True)
                finally:
                    self._timer_auto_stop_handle = None

    def _stop_timer_alarm(self, reason: str) -> None:
        """
        Stop the repeating timer-finished alarm sound and clean up flags.

        This is used for:
        - Stop wake word / explicit stop()
        - Any wake word while timer is ringing (existing behavior)
        - Auto-stop after alarm_duration_seconds

        Thread-safe.
        """
        # CRITICAL FIX: Protect timer state access with lock
        with self._timer_lock:
            if not self._timer_finished:
                return

            _LOGGER.debug("Stopping timer finished sound (%s)", reason)
            self._timer_finished = False
            # Clear handle while holding lock
            if self._timer_auto_stop_handle is not None:
                try:
                    self._timer_auto_stop_handle.cancel()
                except (RuntimeError, asyncio.CancelledError) as e:
                    _LOGGER.warning("Timer auto-stop handle already cancelled: %s", e)
                except Exception as e:
                    _LOGGER.error("Unexpected error cancelling timer: %s", e, exc_info=True)
                finally:
                    self._timer_auto_stop_handle = None

        # Audio operations outside lock to avoid holding lock during I/O
        try:
            self.state.tts_player.stop()
        except (OSError, IOError) as e:
            _LOGGER.warning("TTS player stop failed (device issue): %s", e)
        except Exception as e:
            _LOGGER.error("Unexpected error stopping TTS player: %s", e, exc_info=True)
        # Ensure we unduck and remove the stop word from active set
        self.unduck()
        self.state.active_wake_words.discard(self.state.stop_word.id)

    def _auto_stop_timer_alarm(self) -> None:
        """Auto-stop callback fired after alarm_duration_seconds. Thread-safe."""
        # CRITICAL FIX: Check timer state with lock
        with self._timer_lock:
            timer_active = self._timer_finished

        if not timer_active:
            return

        duration = getattr(self.state.preferences, "alarm_duration_seconds", 0)
        _LOGGER.debug(
            "Auto-stopping timer finished alarm after %s seconds", duration
        )
        self._stop_timer_alarm("auto_timeout")

    def _start_conversation(self, wake_word_phrase: str) -> None:
        """Shared helper to start a new conversation run."""
        _LOGGER.debug("Starting conversation: %s", wake_word_phrase)
        self.send_messages(
            [VoiceAssistantRequest(start=True, wake_word_phrase=wake_word_phrase)]
        )
        self._set_state(SatelliteState.LISTENING)
        self._is_streaming_audio = True
        self.state.tts_player.play(self.state.wakeup_sound)

    def wakeup(self, wake_word: Union[MicroWakeWord, OpenWakeWord]) -> None:
        if self._state not in (SatelliteState.IDLE, SatelliteState.STARTING):
            # Existing behavior: ignore wakeup in other states.
            return

        # CRITICAL FIX: Check timer state with lock
        # If a timer alarm is currently ringing, stop it instead of starting
        # a new conversation run.
        with self._timer_lock:
            timer_active = self._timer_finished

        if timer_active:
            self._stop_timer_alarm("wakeup")
            return

        wake_word_phrase = getattr(wake_word, "wake_word", "wake word")
        _LOGGER.debug("Detected wake word: %s", wake_word_phrase)
        self._start_conversation(wake_word_phrase)

    def manual_wakeup(self, phrase: str = "button") -> None:
        """
        Manual wakeup entrypoint (e.g. hardware button) that behaves like a
        wake word, but without requiring a wake-word model instance.
        """
        if self._state not in (SatelliteState.IDLE, SatelliteState.STARTING):
            return

        # CRITICAL FIX: Check timer state with lock
        with self._timer_lock:
            timer_active = self._timer_finished

        if timer_active:
            self._stop_timer_alarm("button")
            return

        _LOGGER.debug("Manual wakeup triggered: %s", phrase)
        self._start_conversation(phrase)

    def stop(self) -> None:
        """
        Called when the Stop wake word is detected (or equivalent).

        For timer alarms:
            - Stop the repeating alarm.
        For TTS:
            - Stop playback and treat as user-aborted response.
        """
        self.state.active_wake_words.discard(self.state.stop_word.id)

        # CRITICAL FIX: Check timer state with lock
        # If the timer alarm is ringing, stop that instead of a TTS run.
        with self._timer_lock:
            timer_active = self._timer_finished

        if timer_active:
            self._stop_timer_alarm("stop_wake_word")
            return

        # Otherwise this is stopping a TTS response.
        self.state.tts_player.stop()
        _LOGGER.debug("TTS response stopped manually")
        self._tts_finished()

    def play_tts(self) -> None:
        if not self._tts_url:
            return

        _LOGGER.debug("Playing TTS response: %s", self._tts_url)
        self.state.active_wake_words.add(self.state.stop_word.id)
        self.state.tts_player.play(self._tts_url, done_callback=self._tts_finished)
        self._tts_url = None

    def duck(self) -> None:
        _LOGGER.debug("Ducking music")
        self.state.music_player.duck()

    def unduck(self) -> None:
        _LOGGER.debug("Unducking music")
        self.state.music_player.unduck()

    def _determine_final_state(self) -> None:
        if self._continue_conversation:
            self.send_messages([VoiceAssistantRequest(start=True)])
            self._is_streaming_audio = True
            _LOGGER.debug("Continuing conversation")
            self._set_state(SatelliteState.LISTENING)
        else:
            self._set_state(SatelliteState.IDLE)

        _LOGGER.debug("Final state determined")

    def _tts_finished(self) -> None:
        self.state.active_wake_words.discard(self.state.stop_word.id)
        self.send_messages([VoiceAssistantAnnounceFinished()])
        _LOGGER.debug("TTS audio playback finished")

        # If this was just an announcement, we are done,
        # or if we received the official Run End.
        if self._is_announcement or self._run_end_received:
            self._determine_final_state()
            self._is_announcement = False
        else:
            if self._state == SatelliteState.RESPONDING:
                self._set_state(SatelliteState.THINKING)

    def _play_timer_finished(self) -> None:
        """
        Play the timer-finished sound in a loop until either:
        - _timer_finished is cleared (Stop/wakeup/auto-timeout), or
        - alarm_duration_seconds == 0 and user explicitly stops it.

        Thread-safe.
        """
        # CRITICAL FIX: Check timer state with lock
        with self._timer_lock:
            timer_active = self._timer_finished

        if not timer_active:
            # Alarm has been cleared; restore audio state.
            self.unduck()
            return

        self.state.tts_player.play(
            self.state.timer_finished_sound,
            done_callback=lambda: call_all(
                lambda: time.sleep(1.0), self._play_timer_finished
            ),
        )

    # -------------------------------------------------------------------------
    # External wake word download helpers
    # -------------------------------------------------------------------------

    async def _download_external_wake_word(
        self, external_wake_word: VoiceAssistantExternalWakeWord
    ) -> Optional[AvailableWakeWord]:
        """Wrapper to run the blocking download in a thread executor."""
        return await self.state.loop.run_in_executor(
            None,
            functools.partial(
                self._download_external_wake_word_sync, external_wake_word
            ),
        )

    # CRITICAL FIX: Add retry logic for network downloads
    @retry_with_backoff(
        max_retries=3,
        initial_delay=2.0,
        backoff_factor=2.0,
        exceptions=(OSError, ConnectionError, TimeoutError)
    )
    def _download_file_with_retry(self, url: str, dest_path: Path) -> bool:
        """Download a file with retry logic. Returns True on success."""
        from urllib.error import URLError, HTTPError

        try:
            with urlopen(url, timeout=10) as request:
                if request.status != 200:
                    raise NetworkError(f"HTTP {request.status} from {url}")

                with open(dest_path, "wb") as dest_file:
                    shutil.copyfileobj(request, dest_file)
            return True
        except (URLError, HTTPError, OSError, ConnectionError, TimeoutError) as e:
            _LOGGER.warning("Download failed for %s: %s", url, e)
            raise NetworkError(f"Failed to download {url}") from e

    def _download_external_wake_word_sync(
        self, external_wake_word: VoiceAssistantExternalWakeWord
    ) -> Optional[AvailableWakeWord]:
        """Blocking download logic with retry, intended to run in an executor."""
        eww_dir = self.state.download_dir / "external_wake_words"
        eww_dir.mkdir(parents=True, exist_ok=True)

        config_path = eww_dir / f"{external_wake_word.id}.json"
        should_download_config = not config_path.exists()

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

        # CRITICAL FIX: Use retry logic for downloads
        if should_download_config or should_download_model:
            _LOGGER.debug(
                "Downloading %s to %s", external_wake_word.url, config_path
            )
            try:
                self._download_file_with_retry(external_wake_word.url, config_path)
            except NetworkError as e:
                _LOGGER.error("Failed to download config after retries: %s", e)
                return None

        if should_download_model:
            parsed_url = urlparse(external_wake_word.url)
            parsed_url = parsed_url._replace(
                path=posixpath.join(
                    posixpath.dirname(parsed_url.path), model_path.name
                )
            )
            model_url = urlunparse(parsed_url)

            _LOGGER.debug("Downloading %s to %s", model_url, model_path)
            try:
                self._download_file_with_retry(model_url, model_path)
            except NetworkError as e:
                _LOGGER.error("Failed to download model after retries: %s", e)
                return None

        return AvailableWakeWord(
            id=external_wake_word.id,
            type=WakeWordType.MICRO_WAKE_WORD,
            wake_word=external_wake_word.wake_word,
            trained_languages=external_wake_word.trained_languages,
            wake_word_path=config_path,
        )

    # -------------------------------------------------------------------------
    # Connection lifecycle
    # -------------------------------------------------------------------------

    def connection_lost(self, exc):
        super().connection_lost(exc)
        self._set_state(SatelliteState.ERROR)
        _LOGGER.info("Disconnected from Home Assistant")
