"""WebSocket peripheral API.

Bridges LVA state to a separate peripheral container (LEDs, buttons, HAT boards).

Protocol (JSON over WebSocket):
  Events  (LVA → peripheral): {"event": "<name>", "data": {...}}
  Commands (peripheral → LVA): {"command": "<name>", "data": {...}}
  Snapshot (on connect):       {"event": "snapshot", "data": {...}}

Feedback events emitted by LVA
-------------------------------
  wake_word_detected
  listening
  stt_text        data: {"text": str}
              Emitted when HA returns the recognised speech transcript.
              Use this to display what the user said on a screen or LED ticker.
  thinking
  tts_text        data: {"text": str}
              Emitted when HA returns the assistant's response text, just
              before TTS audio begins playing.
              Use this to display the assistant's reply on a screen.
  tts_speaking
  tts_finished
  pipeline_error  data: {"reason": <str>}
              Emitted when the voice pipeline reports an error (STT failure,
              intent error, etc.). NOT emitted when HA disconnects — that
              uses the separate ``disconnected`` event below.
              Peripheral containers should show a brief red error animation
              (e.g. 3 red flashes then off) and then return to idle.
  disconnected
              Emitted when the HA TCP connection is lost.
              Connected leds with peripheral containers should
              show a red twinkle / "no connection" animation and keep retrying
              until they see a ``zeroconf`` event with status "connected".
              NOTE: if LVA itself is not running the peripheral container will
              see a WebSocket connection failure on its end — that is also
              a "disconnected" condition to handle with the same animation.
  idle
  muted
  timer_ticking   data: {"id": str, "name": str, "total_seconds": int, "seconds_left": int}
  timer_updated   data: {"id": str, "name": str, "total_seconds": int, "seconds_left": int}
  timer_ringing   data: {"id": str, "name": str, "total_seconds": int, "seconds_left": int}
  media_player_playing  Emitted when HA sends music/media to the music_player
                        (non-announcement playback). Not emitted for TTS or
                        voice pipeline announcements — those use tts_speaking.
  volume_changed        data: {"volume": 0.0–1.0}
  volume_muted          data: {"muted": true/false}
  zeroconf              data: {"status": "getting_started" | "connected"}

Commands accepted from the peripheral container
------------------------------------------------
  start_listening
  stop_pipeline     Abort the active voice pipeline at any phase — listening,
                    thinking, or wake word active. Calls satellite.stop() which
                    cleans up STT streaming, sends VoiceAssistantAnnounceFinished
                    to HA, unducking music, and emits idle to peripherals.
  mute_mic
  unmute_mic
  volume_up
  volume_down
  stop_timer_ringing
  pause_media_player
  resume_media_player
  stop_media_player
  stop_speaking
  button_single_press
  button_double_press
  button_triple_press
  button_long_press
"""

from __future__ import annotations

import asyncio
import json
import logging
from enum import Enum
from typing import TYPE_CHECKING, Any, Dict, Optional, Set

from aioesphomeapi.api_pb2 import MediaPlayerStateResponse  # type: ignore[attr-defined]  # pylint: disable=no-name-in-module
from aioesphomeapi.model import MediaPlayerState  # type: ignore[import]

if TYPE_CHECKING:
    from .models import ServerState

_LOGGER = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Public enumerations
# ---------------------------------------------------------------------------


class LVAEvent(str, Enum):
    """Events broadcast from LVA to peripheral clients."""

    WAKE_WORD_DETECTED = "wake_word_detected"
    LISTENING = "listening"
    STT_TEXT = "stt_text"
    THINKING = "thinking"
    TTS_TEXT = "tts_text"
    TTS_SPEAKING = "tts_speaking"
    TTS_FINISHED = "tts_finished"
    PIPELINE_ERROR = "pipeline_error"
    DISCONNECTED = "disconnected"
    IDLE = "idle"
    MUTED = "muted"
    TIMER_TICKING = "timer_ticking"
    TIMER_UPDATED = "timer_updated"
    TIMER_RINGING = "timer_ringing"
    MEDIA_PLAYER_PLAYING = "media_player_playing"
    VOLUME_CHANGED = "volume_changed"
    VOLUME_MUTED = "volume_muted"
    ZEROCONF = "zeroconf"


class LVACommand(str, Enum):
    """Commands accepted from peripheral clients."""

    START_LISTENING = "start_listening"
    STOP_PIPELINE = "stop_pipeline"
    MUTE_MIC = "mute_mic"
    UNMUTE_MIC = "unmute_mic"
    VOLUME_UP = "volume_up"
    VOLUME_DOWN = "volume_down"
    SET_VOLUME = "set_volume"
    STOP_TIMER_RINGING = "stop_timer_ringing"
    STOP_MEDIA_PLAYER = "stop_media_player"
    STOP_SPEAKING = "stop_speaking"
    PAUSE_MEDIA_PLAYER = "pause_media_player"
    RESUME_MEDIA_PLAYER = "resume_media_player"
    BUTTON_SINGLE_PRESS = "button_single_press"
    BUTTON_DOUBLE_PRESS = "button_double_press"
    BUTTON_TRIPLE_PRESS = "button_triple_press"
    BUTTON_LONG_PRESS = "button_long_press"


# ---------------------------------------------------------------------------
# Server
# ---------------------------------------------------------------------------


class PeripheralAPIServer:
    """
    WebSocket server that bridges LVA state to a peripheral container.

    Usage
    -----
    1. Construct in ``__main__.main()``.
    2. ``peripheral_api.set_state(state)`` once ``ServerState`` exists.
    3. ``await peripheral_api.start()`` inside the running event loop.
    4. Call ``emit_event_sync()`` from any thread (mpv callbacks, audio thread).
    """

    DEFAULT_VOLUME_STEP: float = 0.05

    def __init__(
        self,
        host: str = "0.0.0.0",
        port: int = 6055,
        volume_step: float = DEFAULT_VOLUME_STEP,
    ) -> None:
        self._host = host
        self._port = port
        self._volume_step = volume_step

        self._clients: Set[Any] = set()
        self._state: Optional[ServerState] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._server: Any = None

        # Last conversation exchange — sent in the snapshot to newly-connecting clients
        self._last_stt_text: Optional[str] = None
        self._last_tts_text: Optional[str] = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def set_state(self, state: "ServerState") -> None:
        """Attach the shared ``ServerState`` so commands can read/mutate it."""
        self._state = state

    async def start(self) -> None:
        """Start the WebSocket server inside the running event loop."""
        try:
            from websockets.server import serve  # type: ignore[import]
        except ImportError:
            _LOGGER.error("websockets package not installed – peripheral API disabled. " "Install with: pip install websockets")
            return

        self._loop = asyncio.get_running_loop()
        self._server = await serve(self._handle_client, self._host, self._port)
        _LOGGER.info("Peripheral API listening at ws://%s:%d", self._host, self._port)

    async def stop(self) -> None:
        """Gracefully shut down the server and all client connections."""
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            _LOGGER.info("Peripheral API stopped")

    # ------------------------------------------------------------------
    # Client handling
    # ------------------------------------------------------------------

    async def _handle_client(self, websocket: Any) -> None:
        addr = getattr(websocket, "remote_address", "unknown")
        self._clients.add(websocket)
        _LOGGER.info("Peripheral client connected: %s", addr)

        await self._send_snapshot(websocket)

        try:
            async for raw in websocket:
                await self._dispatch_command(raw)
        except Exception as exc:  # pylint: disable=broad-except
            _LOGGER.debug("Peripheral client %s error: %s", addr, exc)
        finally:
            self._clients.discard(websocket)
            _LOGGER.info("Peripheral client disconnected: %s", addr)

    async def _send_snapshot(self, websocket: Any) -> None:
        """Push current LVA state to a newly connected peripheral client."""
        state = self._state
        if state is None:
            return

        payload = json.dumps(
            {
                "event": "snapshot",
                "data": {
                    "muted": state.muted,
                    "volume": round(state.volume, 3),
                    "ha_connected": state.connected,
                    "last_stt_text": self._last_stt_text,
                    "last_tts_text": self._last_tts_text,
                },
            }
        )
        try:
            await websocket.send(payload)
        except Exception:  # pylint: disable=broad-except
            pass

    # ------------------------------------------------------------------
    # Command dispatch
    # ------------------------------------------------------------------

    async def _dispatch_command(self, raw: str) -> None:
        """Parse and execute a JSON command from the peripheral container."""
        try:
            msg: Dict[str, Any] = json.loads(raw)
        except json.JSONDecodeError:
            _LOGGER.warning("Peripheral: invalid JSON: %.200s", raw)
            return

        command: str = msg.get("command", "")
        if not command:
            return

        _LOGGER.debug("Peripheral command received: %s", command)

        state = self._state
        if state is None:
            return

        satellite = state.satellite

        if command == LVACommand.START_LISTENING:
            if satellite is None or state.muted:
                return
            satellite.start_listening()  # plays sound, then starts pipeline

        elif command == LVACommand.STOP_PIPELINE:
            # Stops the voice pipeline at any active phase:
            # listening, thinking, or wake word active.
            if satellite is not None:
                satellite.stop()

        elif command == LVACommand.MUTE_MIC:
            if satellite is not None and not state.muted:
                satellite._set_muted(True)  # pylint: disable=protected-access
                await self._push_mute_switch(satellite, muted=True)

        elif command == LVACommand.UNMUTE_MIC:
            if satellite is not None and state.muted:
                satellite._set_muted(False)  # pylint: disable=protected-access
                await self._push_mute_switch(satellite, muted=False)

        elif command in (LVACommand.VOLUME_UP, LVACommand.VOLUME_DOWN):
            delta = self._volume_step if command == LVACommand.VOLUME_UP else -self._volume_step
            new_vol = max(0.0, min(1.0, state.volume + delta))
            vol_pct = int(round(new_vol * 100))

            state.music_player.set_volume(vol_pct)
            state.tts_player.set_volume(vol_pct)

            if state.media_player_entity is not None:
                state.media_player_entity.volume = new_vol
                state.media_player_entity.previous_volume = new_vol

                # Push the new volume to HA so its media player entity updates in real time
                if satellite is not None:

                    satellite.send_messages(
                        [
                            MediaPlayerStateResponse(
                                key=state.media_player_entity.key,
                                state=state.media_player_entity.state,
                                volume=new_vol,
                                muted=state.media_player_entity.muted,
                            )
                        ]
                    )

            # persist_volume also emits VOLUME_CHANGED via models.py
            state.persist_volume(new_vol)

        elif command == LVACommand.SET_VOLUME:
            data = msg.get("data", {})
            volume = data.get("volume")
            if not isinstance(volume, (int, float)):
                _LOGGER.warning("Peripheral: invalid volume in set_volume command: %s", volume)
                return

            new_vol = max(0.0, min(1.0, float(volume)))
            vol_pct = int(round(new_vol * 100))

            state.music_player.set_volume(vol_pct)
            state.tts_player.set_volume(vol_pct)

            if state.media_player_entity is not None:
                state.media_player_entity.volume = new_vol
                state.media_player_entity.previous_volume = new_vol

                # Push the new volume to HA so its media player entity updates in real time
                if satellite is not None:

                    satellite.send_messages(
                        [
                            MediaPlayerStateResponse(
                                key=state.media_player_entity.key,
                                state=state.media_player_entity.state,
                                volume=new_vol,
                                muted=state.media_player_entity.muted,
                            )
                        ]
                    )

            # persist_volume also emits VOLUME_CHANGED via models.py
            state.persist_volume(new_vol)

        elif command == LVACommand.STOP_TIMER_RINGING:
            if satellite is None:
                return
            if getattr(satellite, "_timer_finished", False):
                satellite._timer_finished = False  # pylint: disable=protected-access
                state.active_wake_words.discard(state.stop_word.id)
                state.tts_player.stop()
                satellite.unduck()
                await self.emit_event(LVAEvent.IDLE)

        elif command == LVACommand.STOP_MEDIA_PLAYER:
            state.music_player.stop()
            if state.media_player_entity is not None:

                state.media_player_entity.state = MediaPlayerState.IDLE
                if satellite is not None:
                    satellite.send_messages([self._create_media_player_response(MediaPlayerState.IDLE)])

        elif command == LVACommand.PAUSE_MEDIA_PLAYER:
            state.music_player.pause()
            if state.media_player_entity is not None:
                state.media_player_entity.state = MediaPlayerState.PAUSED
                if satellite is not None:
                    satellite.send_messages([self._create_media_player_response(MediaPlayerState.PAUSED)])

        elif command == LVACommand.RESUME_MEDIA_PLAYER:
            state.music_player.resume()
            if state.media_player_entity is not None:
                state.media_player_entity.state = MediaPlayerState.PLAYING
                if satellite is not None:
                    satellite.send_messages([self._create_media_player_response(MediaPlayerState.PLAYING)])

        elif command == LVACommand.STOP_SPEAKING:
            # Stop TTS/announcement playback and clean up pipeline state.
            # Delegates to satellite.stop() which handles unducking, clearing
            # the stop word, sending VoiceAssistantAnnounceFinished, and
            # emitting TTS_FINISHED + IDLE to peripherals.
            if satellite is not None:
                satellite.stop()

        elif command == LVACommand.BUTTON_SINGLE_PRESS:
            if state.button_event_sensor_entity is not None:
                state.button_event_sensor_entity.update_state("single_press")
                if satellite is not None:
                    satellite.send_messages([
                        state.button_event_sensor_entity._get_state_message()  # pylint: disable=protected-access
                    ])

        elif command == LVACommand.BUTTON_DOUBLE_PRESS:
            state.tts_player.play(state.button_double_press_sound)
            if state.button_event_sensor_entity is not None:
                state.button_event_sensor_entity.update_state("double_press")
                if satellite is not None:
                    satellite.send_messages([
                        state.button_event_sensor_entity._get_state_message()  # pylint: disable=protected-access
                    ])

        elif command == LVACommand.BUTTON_TRIPLE_PRESS:
            state.tts_player.play(state.button_triple_press_sound)
            if state.button_event_sensor_entity is not None:
                state.button_event_sensor_entity.update_state("triple_press")
                if satellite is not None:
                    satellite.send_messages([
                        state.button_event_sensor_entity._get_state_message()  # pylint: disable=protected-access
                    ])

        elif command == LVACommand.BUTTON_LONG_PRESS:
            state.tts_player.play(state.button_long_press_sound)
            if state.button_event_sensor_entity is not None:
                state.button_event_sensor_entity.update_state("long_press")
                if satellite is not None:
                    satellite.send_messages([
                        state.button_event_sensor_entity._get_state_message()  # pylint: disable=protected-access
                    ])

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    async def _push_mute_switch(self, satellite: Any, *, muted: bool) -> None:
        """Reflect a peripheral-triggered mute change to Home Assistant."""
        state = self._state
        if state is None or state.mute_switch_entity is None:
            return

        entity = state.mute_switch_entity
        entity._switch_state = muted  # pylint: disable=protected-access

        # pylint: disable=no-name-in-module
        from aioesphomeapi.api_pb2 import SwitchStateResponse  # type: ignore[attr-defined]

        satellite.send_messages([SwitchStateResponse(key=entity.key, state=muted)])

    # ------------------------------------------------------------------
    # Event emission
    # ------------------------------------------------------------------

    async def emit_event(
        self,
        event: LVAEvent,
        data: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Broadcast an event to all connected peripheral clients."""
        # Cache the last conversation text so newly-connecting clients get it in the snapshot
        if event == LVAEvent.STT_TEXT and data:
            self._last_stt_text = data.get("text")
        elif event == LVAEvent.TTS_TEXT and data:
            self._last_tts_text = data.get("text")
        # Clear both sides at the start of a new pipeline run
        elif event == LVAEvent.LISTENING:
            self._last_stt_text = None
            self._last_tts_text = None

        if not self._clients:
            return

        payload: Dict[str, Any] = {"event": event.value}
        if data:
            payload["data"] = data

        raw_msg = json.dumps(payload)
        dead: Set[Any] = set()

        for ws in list(self._clients):
            try:
                await ws.send(raw_msg)
            except Exception:  # pylint: disable=broad-except
                dead.add(ws)

        self._clients -= dead
        _LOGGER.debug(
            "Peripheral event %-25s → %d client(s)",
            event.value,
            len(self._clients),
        )

    def emit_event_sync(
        self,
        event: LVAEvent,
        data: Optional[Dict[str, Any]] = None,
    ) -> None:
        """
        Thread-safe fire-and-forget event emission.

        Safe to call from mpv callbacks, the audio processing thread, or any
        non-async context while the asyncio event loop is running.
        """
        loop = self._loop
        if loop is None or loop.is_closed():
            return
        asyncio.run_coroutine_threadsafe(self.emit_event(event, data), loop)

    def _create_media_player_response(self, state: MediaPlayerState) -> MediaPlayerStateResponse:
        """Create a MediaPlayerStateResponse with current entity state."""
        media_entity = self._state.media_player_entity
        return MediaPlayerStateResponse(
            key=media_entity.key,
            state=state,
            volume=media_entity.volume,
            muted=media_entity.muted,
        )
