"""A fake Home Assistant for the end-to-end tests.

It talks the ESPHome native API to LVA like Home Assistant's Assist integration does: it subscribes to the voice assistant,
receives the streamed microphone audio and answers a voice request with the same sequence of pipeline events.
Text to speech is served by a small HTTP server, so LVA plays real audio from a URL.
"""

# pylint: disable=unused-argument

import asyncio
import functools
import threading
from dataclasses import dataclass
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, List, Optional

from aioesphomeapi import (
    APIClient,
    EntityInfo,
    EntityState,
    VoiceAssistantAnnounceFinished,
    VoiceAssistantAudioSettings,
    VoiceAssistantEventType,
    VoiceAssistantTimerEventType,
)


@dataclass(frozen=True)
class PipelineStart:
    """A voice request of LVA, sent after a wake word or a button press."""

    conversation_id: str
    flags: int
    wake_word_phrase: Optional[str]


class FixtureServer:
    """Serves a directory over HTTP, the stand-in for the text to speech and media URLs of Home Assistant."""

    def __init__(self, directory: Path) -> None:
        handler = functools.partial(_QuietHandler, directory=str(directory))
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self._server.server_address[1]}"

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._server.shutdown()
        self._server.server_close()


class FakeHomeAssistant:
    """One Home Assistant connection to LVA."""

    def __init__(self) -> None:
        self.starts: List[PipelineStart] = []
        self.stops: int = 0
        self.audio = bytearray()
        self.announce_finished: int = 0
        self.entities: List[EntityInfo] = []
        self.states: Dict[int, Any] = {}
        self._client: Optional[APIClient] = None
        self._unsubscribe: Any = None
        self._announce_finished_event = asyncio.Event()

    async def connect(self, port: int) -> None:
        self._client = APIClient("127.0.0.1", port, None, client_info="e2e-fake-home-assistant")
        await self._client.connect(login=True)
        entities, _ = await self._client.list_entities_services()
        self.entities = list(entities)
        self._client.subscribe_states(self._on_state)
        self._unsubscribe = self._client.subscribe_voice_assistant(
            handle_start=self._handle_start,
            handle_stop=self._handle_stop,
            handle_audio=self._handle_audio,
            handle_announcement_finished=self._handle_announce_finished,
        )

    async def disconnect(self) -> None:
        if self._client is not None:
            await self._client.disconnect()
            self._client = None

    @property
    def client(self) -> APIClient:
        assert self._client is not None, "not connected"
        return self._client

    async def device_info(self) -> Any:
        return await self.client.device_info()

    def entity_by_name(self, name: str) -> EntityInfo:
        """
        Find an entity by its display name.
        :param name: Name as shown on the Home Assistant device page.
        """
        for entity in self.entities:
            if entity.name == name:
                return entity
        raise KeyError(f"no entity named {name!r}, have {[entity.name for entity in self.entities]}")

    async def wait_for_start(self, count: int, timeout: float) -> PipelineStart:
        """Wait until LVA has requested the given number of voice pipelines in total."""
        await _wait_until(lambda: len(self.starts) >= count, timeout, f"voice request {count}")
        return self.starts[count - 1]

    def audio_received_since(self, mark: int) -> bytes:
        return bytes(self.audio[mark:])

    def run_pipeline(self, stt_text: str, reply_text: Optional[str], tts_url: Optional[str], continue_conversation: bool = False) -> None:
        """
        Answer the running voice request with the events Home Assistant sends.
        :param stt_text: What Home Assistant understood the user said.
        :param reply_text: Text of the spoken reply, None for a run that ends without a reply.
        :param tts_url: URL of the reply audio, None for no spoken reply.
        :param continue_conversation: Ask LVA to listen again after the reply.
        """
        self.stop_streaming()
        self._event(VoiceAssistantEventType.VOICE_ASSISTANT_STT_END, {"text": stt_text})
        self._event(VoiceAssistantEventType.VOICE_ASSISTANT_INTENT_START)
        intent_end = {"continue_conversation": "1"} if continue_conversation else {}
        self._event(VoiceAssistantEventType.VOICE_ASSISTANT_INTENT_END, intent_end)
        if reply_text is not None and tts_url is not None:
            self._event(VoiceAssistantEventType.VOICE_ASSISTANT_TTS_START, {"text": reply_text})
            self._event(VoiceAssistantEventType.VOICE_ASSISTANT_TTS_END, {"url": tts_url})
        self._event(VoiceAssistantEventType.VOICE_ASSISTANT_RUN_END)

    def start_run(self) -> None:
        """Send what Home Assistant sends first when it accepts a voice request."""
        self._event(VoiceAssistantEventType.VOICE_ASSISTANT_RUN_START)
        self._event(VoiceAssistantEventType.VOICE_ASSISTANT_STT_START)
        self._event(VoiceAssistantEventType.VOICE_ASSISTANT_STT_VAD_START)

    def stop_streaming(self) -> None:
        """End of speech detected, LVA stops sending microphone audio."""
        self._event(VoiceAssistantEventType.VOICE_ASSISTANT_STT_VAD_END)

    def send_error(self, code: str, message: str) -> None:
        self._event(VoiceAssistantEventType.VOICE_ASSISTANT_ERROR, {"code": code, "message": message})

    async def announce(self, media_url: str, text: str = "", timeout: float = 20.0) -> VoiceAssistantAnnounceFinished:
        """Make LVA play an announcement and wait until it has finished."""
        return await self.client.send_voice_assistant_announcement_await_response(media_url, timeout, text)

    def timer_event(self, event_type: VoiceAssistantTimerEventType, timer_id: str, total_seconds: int, seconds_left: int) -> None:
        self.client.send_voice_assistant_timer_event(event_type, timer_id, "e2e timer", total_seconds, seconds_left, True)

    def _event(self, event_type: VoiceAssistantEventType, data: Optional[Dict[str, str]] = None) -> None:
        self.client.send_voice_assistant_event(event_type, data)

    def _on_state(self, state: EntityState) -> None:
        self.states[state.key] = state

    async def _handle_start(self, conversation_id: str, flags: int, audio_settings: VoiceAssistantAudioSettings, wake_word_phrase: Optional[str]) -> Optional[int]:
        self.starts.append(PipelineStart(conversation_id, flags, wake_word_phrase))
        self.start_run()
        return 0

    async def _handle_stop(self, abort: bool) -> None:
        self.stops += 1

    async def _handle_audio(self, data: bytes, data2: Optional[bytes]) -> None:
        self.audio.extend(data)

    async def _handle_announce_finished(self, finished: Any) -> None:
        self.announce_finished += 1
        self._announce_finished_event.set()


async def _wait_until(condition: Any, timeout: float, description: str) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while not condition():
        if loop.time() >= deadline:
            raise TimeoutError(f"timed out after {timeout} s waiting for {description}")
        await asyncio.sleep(0.05)


class _QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, format: str, *args: Any) -> None:  # pylint: disable=redefined-builtin
        return
