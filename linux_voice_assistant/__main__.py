#!/usr/bin/env python3

import argparse
import os
import asyncio
import json
import logging
import threading
import time
from collections.abc import Iterable
from dataclasses import dataclass
from queue import Queue
from typing import Dict, List, Optional

# Models that should NOT appear in UI dropdowns
EXCLUDED_OWW_MODELS = {"embedding_model", "melspectrogram"}

import re

def _prettify_model_name(name: str) -> str:
    stem = Path(name).stem
    stem = re.sub(r"(?:_v\d+(?:\.\d+)?)$", "", stem)
    parts = stem.split("_")
    if parts and parts[0].lower() == "ok":
        parts[0] = "OK"
    else:
        parts = [p.capitalize() for p in parts]
    return " ".join(parts)



# pylint: disable=no-name-in-module
import sounddevice as sd
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

from .api_server import APIServer
from .entity import ESPHomeEntity, MediaPlayerEntity
from .microwakeword import MicroWakeWord
from .openwakeword_client import WyomingWakeClient
from .mpv_player import MpvMediaPlayer
from .util import call_all, get_mac, is_arm

from .event_bus import EventBus
from .event_led import LedEvent
from pathlib import Path

# Hide helper models that are not detectors
_OWW_DETECT_MODEL_DENYLIST = {"embedding_model", "melspectrogram"}


def _oww_filter_models(models: list[str]) -> list[str]:
    """Normalize names (strip .tflite/paths), drop helper models, sort & de-dupe."""
    stems = {Path(m).stem for m in models}
    return sorted(m for m in stems if m not in _OWW_DETECT_MODEL_DENYLIST)


_LOGGER = logging.getLogger(__name__)
_MODULE_DIR = Path(__file__).parent
_REPO_DIR = _MODULE_DIR.parent
_WAKEWORDS_DIR = _REPO_DIR / "wakewords"
_SOUNDS_DIR = _REPO_DIR / "sounds"

if is_arm():
    _LIB_DIR = _REPO_DIR / "lib" / "linux_arm64"
else:
    _LIB_DIR = _REPO_DIR / "lib" / "linux_amd64"


@dataclass
class AvailableWakeWord:
    id: str
    wake_word: str
    trained_languages: List[str]
    config_path: Path


@dataclass
class ServerState:
    name: str
    mac_address: str
    audio_queue: "Queue[Optional[bytes]]"
    entities: List[ESPHomeEntity]
    available_wake_words: Dict[str, AvailableWakeWord]
    wake_word: MicroWakeWord
    stop_word: MicroWakeWord
    music_player: MpvMediaPlayer
    tts_player: MpvMediaPlayer
    wakeup_sound: str
    timer_finished_sound: str
    loop: asyncio.AbstractEventLoop
    event_bus: EventBus
    media_player_entity: Optional[MediaPlayerEntity] = None
    satellite: "Optional[VoiceSatelliteProtocol]" = None
    current_remote_wake: Optional[str] = None
    wyoming_wake: "Optional[WyomingWakeClient]" = None
    use_wyoming_wake: bool = False


# -----------------------------------------------------------------------------


class VoiceSatelliteProtocol(APIServer):

    def __init__(self, state: ServerState) -> None:
        super().__init__(state.name)

        self.state = state
        self.state.satellite = self

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

        self._is_streaming_audio = False
        self._tts_url: Optional[str] = None
        self._tts_played = False
        self._continue_conversation = False
        self._timer_finished = False

        self.state.event_bus.publish('ready', {})
        _LOGGER.info('System is ready!')

    def handle_voice_event(
        self, event_type: VoiceAssistantEventType, data: Dict[str, str]
    ) -> None:
        _LOGGER.debug("Voice event: type=%s, data=%s", event_type.name, data)

        self.state.event_bus.publish(f'voice_{event_type.name}', data)

        if event_type == VoiceAssistantEventType.VOICE_ASSISTANT_RUN_START:
            self._tts_url = data.get("url")
            self._tts_played = False
            self._continue_conversation = False
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
                self._tts_finished()

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
                self.state.stop_word.is_active = True
                self._timer_finished = True
                self.duck()
                self._play_timer_finished()

    def handle_message(self, msg: message.Message) -> Iterable[message.Message]:
        _LOGGER.debug(f'message {msg.__name__}')
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

            self.state.stop_word.is_active = True
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
        elif isinstance(msg, (
            ListEntitiesRequest,
            SubscribeHomeAssistantStatesRequest,
            MediaPlayerCommandRequest,
        ),):
            for entity in self.state.entities:
                yield from entity.handle_message(msg)

            if isinstance(msg, ListEntitiesRequest):
                yield ListEntitiesDoneResponse()
        elif isinstance(msg, VoiceAssistantConfigurationRequest):
            yield VoiceAssistantConfigurationResponse(
                available_wake_words=[
                    VoiceAssistantWakeWord(
                        id=ww.id,
                        wake_word=ww.wake_word,
                        trained_languages=ww.trained_languages,
                    )
                    for ww in self.state.available_wake_words.values()
                ],
                active_wake_words=[self.state.current_remote_wake] if self.state.use_wyoming_wake else [self.state.wake_word.id],
                max_active_wake_words=1,
            )
            _LOGGER.info("Connected to Home Assistant")
        elif isinstance(msg, VoiceAssistantSetConfiguration):
            # TODO: support multiple wake words
            for wake_word_id in msg.active_wake_words:
                if wake_word_id == self.state.wake_word.id:
                    # Already active
                    break

                if self.state.use_wyoming_wake and self.state.wyoming_wake:
                    try:
                        self.state.wyoming_wake.set_detect([wake_word_id])
                        _LOGGER.info("Switched Wyoming wake model to: %s", wake_word_id)
                        self.state.current_remote_wake = wake_word_id
                        self.state.wake_word.is_active = False
                    except Exception:
                        _LOGGER.exception("Failed to switch Wyoming wake model")
                    break
                model_info = self.state.available_wake_words.get(wake_word_id)
                if not model_info:
                    continue

                _LOGGER.debug("Loading wake word: %s", model_info.config_path)
                self.state.wake_word = MicroWakeWord.from_config(
                    model_info.config_path,
                    self.state.wake_word.libtensorflowlite_c_path,
                )

                _LOGGER.info("Wake word set: %s", self.state.wake_word.wake_word)
                break

    def handle_audio(self, audio_chunk: bytes) -> None:
        if not self._is_streaming_audio:
            return

        self.send_messages([VoiceAssistantAudio(data=audio_chunk)])

    def wakeup(self) -> None:
        # Why are we stopping the timer? Wouldn't it be better to delay it?
        if self._timer_finished:
            # Stop timer instead
            self._timer_finished = False
            self.state.tts_player.stop()
            _LOGGER.debug("Stopping timer finished sound")
            return

        wake_word_phrase = (self.state.current_remote_wake or self.state.wake_word.wake_word) if self.state.use_wyoming_wake else self.state.wake_word.wake_word
        _LOGGER.debug("Detected wake word: %s", wake_word_phrase)
        try:
            self.state.wyoming_wake.suppress(2500)
        except Exception:
            pass
        self.send_messages(
            [VoiceAssistantRequest(start=True, wake_word_phrase=wake_word_phrase)]
        )

        self.state.event_bus.publish('voice_wakeword', {'wake_word_phrase': wake_word_phrase})


        self.duck()
        self._is_streaming_audio = True
        self.state.tts_player.play(self.state.wakeup_sound)

    def stop(self) -> None:
        self.state.stop_word.is_active = False
        self.state.tts_player.stop()

        if self._timer_finished:
            self._timer_finished = False
            _LOGGER.debug("Stopping timer finished sound")
        else:
            _LOGGER.debug("TTS response stopped manually")
            self._tts_finished()

    def play_tts(self) -> None:
        if (not self._tts_url) or self._tts_played:
            return

        self._tts_played = True
        _LOGGER.debug("Playing TTS response: %s", self._tts_url)

        self.state.event_bus.publish('voice_play_tts', {})

        self.state.stop_word.is_active = True
        self.state.tts_player.play(self._tts_url, done_callback=self._tts_finished)

    def duck(self) -> None:
        _LOGGER.debug("Ducking music")
        self.state.music_player.duck()

    def unduck(self) -> None:
        _LOGGER.debug("Unducking music")
        self.state.music_player.unduck()

    def _tts_finished(self) -> None:
        self.state.stop_word.is_active = False
        self.send_messages([VoiceAssistantAnnounceFinished()])

        # Actual time the TTS stops speaking
        self.state.event_bus.publish('voice__tts_finished', {})

        if self._continue_conversation:
            self.send_messages([VoiceAssistantRequest(start=True)])
            self._is_streaming_audio = True
            _LOGGER.debug("Continuing conversation")
        else:
            self.unduck()

        _LOGGER.debug("TTS response finished")

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


def process_audio(state: ServerState):

    try:
        while True:
            audio_chunk = state.audio_queue.get()
            if audio_chunk is None:
                break

            if state.satellite is None:
                continue

            try:
                state.satellite.handle_audio(audio_chunk)

                if state.use_wyoming_wake:
                    if state.wyoming_wake:
                        state.wyoming_wake.send_audio_chunk(audio_chunk)
                else:
                    if state.wake_word.is_active and state.wake_word.process_streaming(
                        audio_chunk
                    ):
                        state.satellite.wakeup()

                if state.stop_word.is_active and state.stop_word.process_streaming(
                    audio_chunk
                ):
                    state.satellite.stop()
            except Exception:
                _LOGGER.exception("Unexpected error handling audio")

    except Exception:
        _LOGGER.exception("Unexpected error processing audio")


# -----------------------------------------------------------------------------


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--name", required=True)
    parser.add_argument("--wake-uri", help="Wyoming wake server URI (e.g., tcp://127.0.0.1:10400)")
    parser.add_argument("--wake-word-name", help="Wake word name on the Wyoming server (e.g., hal)")
    parser.add_argument("--wake-models-dir", help="Fallback: directory containing openWakeWord *.tflite models to list if the server does not advertise them")
    parser.add_argument(
        "--audio-input-device",
        default="default",
        help="sounddevice name for input device",
    )
    parser.add_argument("--audio-input-block-size", type=int, default=1024)
    parser.add_argument("--audio-output-device", help="mpv name for output device")
    parser.add_argument(
        "--wake-word-dir",
        default=_WAKEWORDS_DIR,
        help="Directory with wake word models (.tflite) and configs (.json)",
    )
    parser.add_argument(
        "--wake-model", default="okay_nabu", help="Id of active wake model"
    )
    parser.add_argument("--stop-model", default="stop", help="Id of stop model")
    parser.add_argument(
        "--wakeup-sound", default=str(_SOUNDS_DIR / "wake_word_triggered.flac")
    )
    parser.add_argument(
        "--timer-finished-sound", default=str(_SOUNDS_DIR / "timer_finished.flac")
    )
    parser.add_argument(
        "--host",
        default="0.0.0.0",
        help="Address for ESPHome server (default: 0.0.0.0)",
    )
    parser.add_argument(
        "--port", type=int, default=6053, help="Port for ESPHome server (default: 6053)"
    )
    parser.add_argument(
        "--debug", action="store_true", help="Print DEBUG messages to console"
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.DEBUG if args.debug else logging.INFO)
    _LOGGER.debug(args)

    # Optional fallback for discovering remote models from filesystem
    models_dir = args.wake_models_dir or os.environ.get("WYOMING_MODELS_DIR")

    use_wyoming = bool(args.wake_uri)
    wyoming_client = None
    if use_wyoming:
        _LOGGER.info("Using Wyoming openWakeWord at %s (name=%s)", args.wake_uri, args.wake_word_name)
        wyoming_client = WyomingWakeClient(args.wake_uri, args.wake_word_name or "")

    # Load available wake words
    wake_word_dir = Path(args.wake_word_dir)
    available_wake_words: Dict[str, AvailableWakeWord] = {}
    for model_config_path in wake_word_dir.glob("*.json"):
        model_id = model_config_path.stem
        if model_id == args.stop_model:
            # Don't show stop model as an available wake word
            continue

        with open(model_config_path, "r", encoding="utf-8") as model_config_file:
            model_config = json.load(model_config_file)
            available_wake_words[model_id] = AvailableWakeWord(
                id=model_id,
                wake_word=model_config["wake_word"],
                trained_languages=model_config.get("trained_languages", []),
                config_path=model_config_path,
            )

    _LOGGER.debug("Available wake words: %s", list(sorted(available_wake_words.keys())))

    if use_wyoming and wyoming_client:
        try:
            remote_models = wyoming_client.list_models() or []
            _LOGGER.debug("Queried Wyoming models: %s", [m.get("name") if isinstance(m, dict) else m for m in remote_models])
        except Exception:
            remote_models = []
            _LOGGER.exception("Failed to list models from Wyoming server")
        if remote_models:
            available_wake_words = {}
            for m in remote_models:
                name = (m.get("name") or "").strip()
                langs = m.get("languages") or m.get("trained_languages") or []
                if not name:
                    continue
                available_wake_words[name] = AvailableWakeWord(
                        id=name,
                        wake_word=(m.get('label') or _prettify_model_name(name)),
                    trained_languages=langs,
                    config_path=Path(f"wyoming:{name}"),
                )
            _LOGGER.debug("Remote OpenWakeWord models: %s", list(sorted(available_wake_words.keys())))

    libtensorflowlite_c_path = _LIB_DIR / "libtensorflowlite_c.so"
    _LOGGER.debug("libtensorflowlite_c path: %s", libtensorflowlite_c_path)

    # Load wake/stop models
    wake_config_path = wake_word_dir / f"{args.wake_model}.json"
    _LOGGER.debug("Loading wake model: %s", wake_config_path)
    wake_model = MicroWakeWord.from_config(wake_config_path, libtensorflowlite_c_path)

    stop_config_path = wake_word_dir / f"{args.stop_model}.json"
    _LOGGER.debug("Loading stop model: %s", stop_config_path)
    stop_model = MicroWakeWord.from_config(stop_config_path, libtensorflowlite_c_path)
    
    loop = asyncio.get_running_loop()

    state = ServerState(
        name=args.name,
        mac_address=get_mac(),
        audio_queue=Queue(),
        entities=[],
        available_wake_words=available_wake_words,
        wake_word=wake_model,
        stop_word=stop_model,
        event_bus=EventBus(),
        loop=loop,
        music_player=MpvMediaPlayer(device=args.audio_output_device),
        tts_player=MpvMediaPlayer(device=args.audio_output_device),
        wakeup_sound=args.wakeup_sound,
        timer_finished_sound=args.timer_finished_sound,
        wyoming_wake=wyoming_client,
        use_wyoming_wake=use_wyoming,
    )

    LedEvent(state)

    # Connect to Wyoming wake server if enabled
    if state.use_wyoming_wake and state.wyoming_wake:
        def _on_detect(_name, _ts):
            if state.satellite is not None:
                state.loop.call_soon_threadsafe(lambda: state.satellite.wakeup())
        state.wyoming_wake.connect(_on_detect)
        # Disable local wake model to avoid double triggers
        state.wake_word.is_active = False
        # Refresh remote models after connect
        try:
            cached = state.wyoming_wake.get_models(timeout=1.5) if state.wyoming_wake else []
            if not cached and models_dir:
                try:
                    from pathlib import Path as _P
                    cached = [{"name": p.stem} for p in _P(models_dir).glob("*.tflite")]
                    cached = [
                        m for m in cached
                        if (m.get('name') or '').strip() not in EXCLUDED_OWW_MODELS
                    ]
                    _LOGGER.info("Using filesystem fallback for OWW models (post-connect): %s", [m["name"] for m in cached])
                except Exception:
                    _LOGGER.exception("Failed to scan wake-models-dir (post-connect)")
            if cached:
                available = {}
                for m in cached:
                    name = (m.get('name') or '').strip()
                    if not name:
                        continue
                    available[name] = AvailableWakeWord(
                        id=name,
                        wake_word=(m.get('label') or _prettify_model_name(name)),
                        trained_languages=[],
                        config_path=Path(f"wyoming:{name}")
                    )
                state.available_wake_words = available
                _LOGGER.debug("Using remote models from cached info: %s", list(sorted(state.available_wake_words.keys())))
        except Exception:
            _LOGGER.exception("Failed to refresh remote models from connection info")
        # Initialize remote detection target if provided
        try:
            if args.wake_word_name:
                state.wyoming_wake.set_detect([args.wake_word_name])
                state.current_remote_wake = args.wake_word_name
            else:
                try:
                    first = next(iter(state.available_wake_words.keys()))
                except StopIteration:
                    first = None
                if first:
                    state.wyoming_wake.set_detect([first])
                    state.current_remote_wake = first
                else:
                    _LOGGER.warning("Wyoming enabled but no remote models available; not sending detect yet")
        except Exception:
            _LOGGER.exception("Failed to initialize remote detect")

    process_audio_thread = threading.Thread(
        target=process_audio, args=(state,), daemon=True
    )
    process_audio_thread.start()

    def sd_callback(indata, _frames, _time, _status):
        state.audio_queue.put_nowait(bytes(indata))

    server = await loop.create_server(
        lambda: VoiceSatelliteProtocol(state), host=args.host, port=args.port
    )

    try:
        _LOGGER.debug("Opening audio input device: %s", args.audio_input_device)
        with sd.RawInputStream(
            samplerate=16000,
            blocksize=args.audio_input_block_size,
            device=args.audio_input_device,
            dtype="int16",
            channels=1,
            callback=sd_callback,
        ):
            async with server:
                _LOGGER.info("Server started (host=%s, port=%s)", args.host, args.port)
                await server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        state.audio_queue.put_nowait(None)
        process_audio_thread.join()

    _LOGGER.debug("Server stopped")


if __name__ == "__main__":
    asyncio.run(main())