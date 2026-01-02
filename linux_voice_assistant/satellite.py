"""Voice satellite protocol."""

import asyncio
import hashlib
import logging
import posixpath
import shutil
import subprocess
import time
from collections.abc import Iterable
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional, Set, Union
from urllib.parse import urlparse, urlunparse
from urllib.request import urlopen, Request
import json

from . import screen

# pylint: disable=no-name-in-module
from aioesphomeapi.api_pb2 import (  # type: ignore[attr-defined]
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
from .entity import MediaPlayerEntity, TextAttributeEntity
from .models import AvailableWakeWord, ServerState, WakeWordType
from .util import call_all

_LOGGER = logging.getLogger(__name__)


def _is_raspberry_pi() -> bool:
    """Detect if running on Raspberry Pi hardware."""
    try:
        # Check device tree model for Raspberry Pi signature
        with open("/proc/device-tree/model", "r", encoding="utf-8") as f:
            model = f.read().strip()
            if "Raspberry Pi" in model:
                return True
    except Exception:
        pass
    
    # Fallback: check for Raspberry Pi in cpuinfo
    try:
        with open("/proc/cpuinfo", "r", encoding="utf-8") as f:
            cpuinfo = f.read()
            if "Raspberry Pi" in cpuinfo:
                return True
    except Exception:
        pass
    
    return False


def _set_led(trigger: str) -> None:
    """Set the Raspberry Pi power LED state.
    
    Args:
        trigger: LED trigger state ('none', 'default-on', 'heartbeat', etc.)
    
    Only runs on Raspberry Pi systems.
    """
    led_path = Path("/sys/class/leds/PWR/trigger")
    if not led_path.exists():
        return  # LED control not available on this system
    
    try:
        subprocess.run(
            ["sudo", "tee", str(led_path)],
            input=f"{trigger}\n".encode(),
            check=True,
            capture_output=True,
        )
    except Exception as e:
        _LOGGER.debug("Could not set LED trigger to %s: %s", trigger, e)


def _set_screen_dpms(timeout: int, display: str = ":0") -> None:
    """Set screen state (cross-platform: X11 xset or WSL PowerShell).
    
    Args:
        timeout: Seconds until screen turns off (0 to force on immediately)
        display: X display to target (X11 only, ignored on WSL)
    """
    if timeout == 0:
        # Wake screen immediately
        _LOGGER.debug("Waking screen")
        screen.screen_on(display)
    else:
        # Sleep screen after timeout
        _LOGGER.debug("Setting screen timeout to %d seconds", timeout)
        screen.screen_off(timeout, display)



class VoiceSatelliteProtocol(APIServer):

    def __init__(self, state: ServerState) -> None:
        super().__init__(state.name)

        self.state = state
        self.state.satellite = self
        self._loop: Optional[asyncio.AbstractEventLoop] = None

        if self.state.media_player_entity is None:
            self.state.media_player_entity = MediaPlayerEntity(
                server=self,
                key=len(state.entities),
                name="Media Player",
                object_id="linux_voice_assistant_media_player",
                music_player=state.music_player,
                announce_player=state.tts_player,
            )
            self.state.entities.append(self.state.media_player_entity)

        if self.state.active_tts_entity is None:
            self.state.active_tts_entity = TextAttributeEntity(
                server=self,
                key=len(state.entities),
                name="Active TTS",
                object_id="active_tts",
            )
            self.state.entities.append(self.state.active_tts_entity)

        if self.state.active_stt_entity is None:
            self.state.active_stt_entity = TextAttributeEntity(
                server=self,
                key=len(state.entities),
                name="Active STT",
                object_id="active_stt",
            )
            self.state.entities.append(self.state.active_stt_entity)

        if self.state.active_assistant_entity is None:
            self.state.active_assistant_entity = TextAttributeEntity(
                server=self,
                key=len(state.entities),
                name="Active Assistant",
                object_id="active_assistant",
            )
            self.state.entities.append(self.state.active_assistant_entity)

        self._is_streaming_audio = False
        self._tts_url: Optional[str] = None
        self._tts_played = False
        self._continue_conversation = False
        self._timer_finished = False
        self._external_wake_words: Dict[str, VoiceAssistantExternalWakeWord] = {}
        self._current_assistant_name: str = "Assistant"
        self._is_rpi = _is_raspberry_pi()
        self._screen_management = state.screen_management
        
        _LOGGER.info("Screen management enabled: %s", self._screen_management)
        # Set LED to idle state on init (only on RPi)
        if self._is_rpi:
            _set_led("none")

    def handle_voice_event(
        self, event_type: VoiceAssistantEventType, data: Dict[str, str]
    ) -> None:
        _LOGGER.debug("Voice event: type=%s, data=%s", event_type.name, data)
        
        # Log conversation start/end at INFO for external monitoring
        if event_type in (VoiceAssistantEventType.VOICE_ASSISTANT_RUN_START,
                          VoiceAssistantEventType.VOICE_ASSISTANT_TTS_END):
            _LOGGER.info("Voice event: %s", event_type.name)

        if event_type == VoiceAssistantEventType.VOICE_ASSISTANT_RUN_START:
            self._tts_url = data.get("url")
            self._tts_played = False
            self._continue_conversation = False
            self._update_active_stt("")
            self._update_active_tts("")
            if self._is_rpi:
                _set_led("default-on")  # Listening
            if self._screen_management:
                _LOGGER.info("Waking screen for voice interaction")
                _set_screen_dpms(0)  # Wake screen immediately
        elif event_type == VoiceAssistantEventType.VOICE_ASSISTANT_STT_START:
            self._update_active_stt("")
            if self._is_rpi:
                _set_led("heartbeat")  # Processing speech
        elif event_type == VoiceAssistantEventType.VOICE_ASSISTANT_STT_VAD_START:
            self._update_active_stt("")
        elif event_type == VoiceAssistantEventType.VOICE_ASSISTANT_STT_VAD_END:
            self._is_streaming_audio = False
            self._update_active_stt("")
        elif event_type == VoiceAssistantEventType.VOICE_ASSISTANT_STT_END:
            self._is_streaming_audio = False
            stt_text = data.get("text", data.get("stt", ""))
            self._update_active_stt(stt_text)
            self._log_to_file(f"User: {stt_text}")
        elif event_type == VoiceAssistantEventType.VOICE_ASSISTANT_INTENT_PROGRESS:
            if data.get("tts_start_streaming") == "1":
                # Start streaming early
                self.play_tts()
        elif event_type == VoiceAssistantEventType.VOICE_ASSISTANT_INTENT_END:
            if data.get("continue_conversation") == "1":
                self._continue_conversation = True
        elif event_type == VoiceAssistantEventType.VOICE_ASSISTANT_TTS_START:
            tts_text = data.get("text", "")
            self._update_active_tts(tts_text)
            self._log_to_file(f"{self._current_assistant_name}: {tts_text}")
            self._sync_history_to_ha()
            if self._is_rpi:
                _set_led("default-on")  # Responding with TTS
        elif event_type == VoiceAssistantEventType.VOICE_ASSISTANT_TTS_END:
            self._tts_url = data.get("url")
            self.play_tts()
        elif event_type == VoiceAssistantEventType.VOICE_ASSISTANT_RUN_END:
            self._is_streaming_audio = False
            if not self._tts_played:
                self._tts_finished()

            self._tts_played = False
            if self._is_rpi:
                _set_led("default-on")  # Back to idle

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
                self.duck()
                self._play_timer_finished()

    def handle_message(self, msg: message.Message) -> Iterable[message.Message]:
        if isinstance(msg, VoiceAssistantEventResponse):
            # Pipeline event
            data: Dict[str, str] = {}
            for arg in msg.data:
                data[arg.name] = arg.value

            self.handle_voice_event(VoiceAssistantEventType(msg.event_type), data)
        elif isinstance(msg, VoiceAssistantAnnounceRequest):
            _LOGGER.debug("Announcing: %s", msg.text)

            assert self.state.media_player_entity is not None

            urls = []
            if msg.preannounce_media_id:
                urls.append(msg.preannounce_media_id)

            urls.append(msg.media_id)

            self._update_active_tts(msg.text)

            self.state.active_wake_words.add(self.state.stop_word.id)
            self._continue_conversation = msg.start_conversation

            self.duck()
            yield from self.state.media_player_entity.play(
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

            # Store event loop reference for callbacks from other threads
            if self._loop is None:
                try:
                    self._loop = asyncio.get_running_loop()
                except RuntimeError:
                    pass
            
            yield VoiceAssistantConfigurationResponse(
                available_wake_words=available_wake_words,
                active_wake_words=[
                    ww.id
                    for ww in self.state.wake_words.values()
                    if ww.id in self.state.active_wake_words
                ],
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

        if not self._is_streaming_audio:
            return

        self.send_messages([VoiceAssistantAudio(data=audio_chunk)])

    def wakeup(self, wake_word: Union[MicroWakeWord, OpenWakeWord]) -> None:
        if self._timer_finished:
            # Stop timer instead
            self._timer_finished = False
            self.state.tts_player.stop()
            _LOGGER.debug("Stopping timer finished sound")
            return

        wake_word_phrase = wake_word.wake_word
        _LOGGER.debug("Detected wake word: %s", wake_word_phrase)
        
        # Use friendly name if available
        wake_word_id = wake_word.id if hasattr(wake_word, 'id') else None
        friendly_name = self.state.global_preferences.wake_word_friendly_names.get(wake_word_id, wake_word_phrase)
        self._current_assistant_name = friendly_name
        
        self._update_active_assistant(friendly_name)
        self.send_messages(
            [VoiceAssistantRequest(start=True, wake_word_phrase=wake_word_phrase)]
        )
        self.duck()
        self._is_streaming_audio = True
        self.state.tts_player.play(self.state.wakeup_sound)

    def stop(self) -> None:
        self.state.active_wake_words.discard(self.state.stop_word.id)
        self.state.tts_player.stop()
        self._update_active_tts("")
        self._update_active_stt("")

        if self._timer_finished:
            self._timer_finished = False
            _LOGGER.debug("Stopping timer finished sound")
        else:
            _LOGGER.debug("TTS response stopped manually")
            self._tts_finished()

    def play_tts(self) -> None:
        if (not self._tts_url) or self._tts_played:
            return
        # NEW: Check if the URL contains "wake_chime" or "acknowledge"
        # Home Assistant system chimes usually have distinct paths
        if "ack" in self._tts_url or "chime" in self._tts_url:
            _LOGGER.debug("Skipping HA-provided chime: %s", self._tts_url)
            self._tts_played = True # Mark as played so it doesn't loop
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
        self.state.active_wake_words.discard(self.state.stop_word.id)
        self.send_messages([VoiceAssistantAnnounceFinished()])

        if self._continue_conversation:
            self.duck()
            self.state.tts_player.play(self.state.wakeup_sound)
            self.send_messages([VoiceAssistantRequest(start=True)])
            self._is_streaming_audio = True
            if self._is_rpi:
                _set_led("default-on")  # Back to listening
            _LOGGER.debug("Continuing conversation")
        else:
            self.unduck()
            if self._is_rpi:
                _set_led("none")  # Back to idle
            if self._screen_management:
                _LOGGER.info("Setting screen sleep timeout after TTS playback finished")
                _set_screen_dpms(10)  # 10 second timeout
            # Clear sensors after 5 seconds (using stored loop reference)
            if self._loop is not None:
                self._loop.call_later(5.0, self._clear_sensors)
            else:
                _LOGGER.warning("No event loop available for delayed sensor clearing")

        _LOGGER.debug("TTS response finished")

    def _clear_sensors(self) -> None:
        """Clear all text sensors."""
        _LOGGER.debug("Clearing sensors after delay")
        self._update_active_tts("")
        self._update_active_stt("")
        self._update_active_assistant("")

    def _play_timer_finished(self) -> None:
        if not self._timer_finished:
            self.unduck()
            return

        self.state.tts_player.play(
            self.state.timer_finished_sound,
            done_callback=lambda: call_all(
                lambda: time.sleep(1.0), self._play_timer_finished
            ),
        )

    def connection_lost(self, exc):
        super().connection_lost(exc)
        _LOGGER.info("Disconnected from Home Assistant")
        if self._is_rpi:
            _set_led("none")  # LED off when disconnected or idle

    def _update_active_tts(self, text: str) -> None:
        if self.state.active_tts_entity is None:
            return

        _LOGGER.debug("Updating active_tts to: %r", text)
        msg = self.state.active_tts_entity.update(text)
        self.send_messages([msg])

    def _update_active_stt(self, text: str) -> None:
        if self.state.active_stt_entity is None:
            return

        _LOGGER.debug("Updating active_stt to: %r", text)
        msg = self.state.active_stt_entity.update(text)
        self.send_messages([msg])

    def _update_active_assistant(self, text: str) -> None:
        if self.state.active_assistant_entity is None:
            return

        _LOGGER.debug("Updating active_assistant to: %r", text)
        msg = self.state.active_assistant_entity.update(text)
        self.send_messages([msg])

    def _log_to_file(self, message: str) -> None:
        """Log a message to the unified lvas_log file with timestamp."""
        try:
            # Use the symlink if available, otherwise construct the path
            log_path = Path(__file__).parent.parent / "lvas_log"
            if not log_path.exists():
                log_path = Path("/dev/shm/lvas_log")
            if not log_path.exists():
                log_path = Path("/tmp/lvas_log")
            
            timestamp = datetime.now().strftime("%Y_%m_%d %H:%M:%S")
            log_entry = f"[{timestamp}] -- {message}\n"
            
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(log_entry)
        except Exception as e:
            _LOGGER.warning("Failed to write to log file: %s", e)

    def _sync_history_to_ha(self) -> None:
        """Sync last 100 lines of log to Home Assistant."""
        ha_url = self.state.global_preferences.ha_base_url
        ha_token = self.state.global_preferences.ha_token
        ha_entity = self.state.global_preferences.ha_history_entity or "input_text.lvas_history"
        
        if not ha_url or not ha_token:
            _LOGGER.debug("HA sync disabled: ha_base_url=%s, ha_token=%s", ha_url, bool(ha_token))
            return
        
        try:
            # Read last 100 lines from log
            log_path = Path(__file__).parent.parent / "lvas_log"
            if not log_path.exists():
                log_path = Path("/dev/shm/lvas_log")
            if not log_path.exists():
                log_path = Path("/tmp/lvas_log")
            
            if not log_path.exists():
                _LOGGER.debug("Log file not found for HA sync")
                return
            
            with open(log_path, "r", encoding="utf-8") as f:
                lines = f.readlines()
            
            # Get last 100 lines
            history_lines = lines[-100:] if len(lines) > 100 else lines
            history_text = "".join(history_lines)
            
            # Send to HA
            url = f"{ha_url}/api/states/{ha_entity}"
            headers = {
                "Authorization": f"Bearer {ha_token}",
                "Content-Type": "application/json"
            }
            data = {
                "state": f"Updated {datetime.now().strftime('%H:%M:%S')}",
                "attributes": {"history": history_text}
            }
            
            _LOGGER.debug("Syncing history to HA: %s (%d lines)", ha_entity, len(history_lines))
            
            req = Request(
                url,
                data=json.dumps(data).encode('utf-8'),
                headers=headers,
                method='POST'
            )
            with urlopen(req) as response:
                if response.status == 200 or response.status == 201:
                    _LOGGER.debug("History synced to HA successfully")
                else:
                    _LOGGER.warning("Failed to sync history to HA: status %s", response.status)
        except Exception as e:
            _LOGGER.warning("Failed to sync history to HA: %s", e)

    def _download_external_wake_word(
        self, external_wake_word: VoiceAssistantExternalWakeWord
    ) -> Optional[AvailableWakeWord]:
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
                path=posixpath.join(posixpath.dirname(parsed_url.path), model_path.name)
            )
            model_url = urlunparse(parsed_url)

            _LOGGER.debug("Downloading %s to %s", model_url, model_path)
            with urlopen(model_url) as request:
                if request.status != 200:
                    _LOGGER.warning(
                        "Failed to download: %s, status=%s", model_url, request.status
                    )
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
