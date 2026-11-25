"""WebSocket peripheral API, version 1.

This is the API as it was before versioning. It is frozen: it only gets bug fixes. New features go into a newer version.
The event ``timer_auto_stopped`` was added as a bug fix: a timer that stopped ringing on its own was never reported.

Bridges LVA state to a separate peripheral container (LEDs, buttons, HAT boards).

Protocol (JSON over WebSocket):
  Events  (LVA → peripheral): {"event": "<name>", "data": {...}}
  Commands (peripheral → LVA): {"command": "<name>", "data": {...}}
  Snapshot (on connect):       {"event": "snapshot", "data": {...}}

Feedback events emitted by LVA
-------------------------------
  wake_word_detected  data: {"wake_word_id": str, "slot": int, "score": float | null}
              ``slot`` is 0 or 1 (wake word slot), ``score`` the detection
              probability (0.0–1.0) that exceeded the sensitivity threshold.
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
  muted                 data: {"muted": true/false}
  timer_ticking   data: {"id": str, "name": str, "total_seconds": int, "seconds_left": int}
  timer_updated   data: {"id": str, "name": str, "total_seconds": int, "seconds_left": int}
  timer_ringing   data: {"id": str, "name": str, "total_seconds": int, "seconds_left": int}
  timer_auto_stopped    data: {"seconds": int}
              The ringing timer stopped on its own after ``--timer-max-ring-seconds``
              (``seconds`` is how long it rang). Followed by ``idle``.
  media_player_playing  Emitted when HA sends music/media to the music_player
                        (non-announcement playback). Not emitted for TTS or
                        voice pipeline announcements — those use tts_speaking.
  volume_changed        data: {"volume": 0.0–1.0}
  volume_muted          data: {"muted": true/false}
  zeroconf              data: {"status": "getting_started" | "connected"}
  light_command         data: {"object_id": str, "state": bool, "brightness": float,
                              "red": float, "green": float, "blue": float, "effect": str}
              Fires when HA changes a Light entity that a peripheral
              previously registered via register_light. The peripheral
              matches on object_id and applies the new state. The
              effect names are those the peripheral declared at
              registration; e.g. "Voice Assistant" runs the pipeline
              animations.
  button_lock_changed   data: {"locked": bool}
              Fires when the user toggles the "Disable button controls"
              switch on the Home Assistant device page for a peripheral
              that registered it via register_button_lock. The current
              value is also included as "button_controls_locked" in the
              snapshot payload for newly-connecting/reconnecting clients.
              A peripheral should stop executing button-triggered LVA
              commands (start_listening, stop_pipeline, mute_mic/
              unmute_mic, volume_up/down, button_*_press, etc.) while
              locked is true.

Commands accepted from the peripheral container
------------------------------------------------
  start_listening
  stop_pipeline     Abort the active voice pipeline at any phase — listening,
                    thinking, speaking or wake word active. Calls satellite.stop() which
                    cleans up STT streaming, sends VoiceAssistantAnnounceFinished
                    to HA, unducking music, and emits idle to peripherals.
  mute_mic
  unmute_mic
  volume_up
  volume_down
  set_volume        data: {"volume": 0.0–1.0}
  stop_timer_ringing
  pause_media_player
  resume_media_player
  stop_media_player
  button_single_press
  button_double_press
  button_triple_press
  button_long_press
  register_light    data: {"name": str, "object_id": str, "effects": [str],
                           "supports_rgb": bool, "supports_brightness": bool}
              The peripheral declares an LED Light it wants exposed in
              HA. LVA creates a matching ESPHome Light entity (visible
              as light.<satellite>_<object_id>) and routes HA changes
              back to the peripheral as light_command events. Send
              once after connecting; duplicate registrations for the
              same object_id are ignored.
  register_button
              The peripheral declares that it has physical buttons and
              wants a Button Press event entity exposed in HA. LVA
              creates a ButtonEventSensorEntity (visible as
              event.<satellite>_button_press_event) that fires
              single_press, double_press, triple_press, and long_press
              events to Home Assistant when the corresponding
              button_* commands are sent. Send once after connecting;
              duplicate registrations are ignored.
  register_button_lock
              The peripheral declares that it has on-board buttons whose
              behavior can be toggled from Home Assistant. LVA creates a
              "Disable button controls" switch entity (visible as
              switch.<satellite>_disable_button_controls, disabled/off
              by default) and routes HA changes back to the peripheral
              as button_lock_changed events. The peripheral is
              responsible for honoring the lock state — LVA does not
              gate anything on its end. Send once after connecting;
              duplicate registrations are ignored and preserve the
              current lock state.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from enum import Enum
from typing import TYPE_CHECKING, Any, Dict, Optional, Set

from aioesphomeapi.api_pb2 import MediaPlayerStateResponse  # type: ignore[attr-defined]  # pylint: disable=no-name-in-module

if TYPE_CHECKING:
    from ..models import ServerState

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
    TIMER_AUTO_STOPPED = "timer_auto_stopped"
    MEDIA_PLAYER_PLAYING = "media_player_playing"
    VOLUME_CHANGED = "volume_changed"
    VOLUME_MUTED = "volume_muted"
    ZEROCONF = "zeroconf"
    LIGHT_COMMAND = "light_command"
    BUTTON_LOCK_CHANGED = "button_lock_changed"


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
    PAUSE_MEDIA_PLAYER = "pause_media_player"
    RESUME_MEDIA_PLAYER = "resume_media_player"
    BUTTON_SINGLE_PRESS = "button_single_press"
    BUTTON_DOUBLE_PRESS = "button_double_press"
    BUTTON_TRIPLE_PRESS = "button_triple_press"
    BUTTON_LONG_PRESS = "button_long_press"
    REGISTER_LIGHT = "register_light"
    REGISTER_BUTTON = "register_button"
    REGISTER_BUTTON_LOCK = "register_button_lock"


# ---------------------------------------------------------------------------
# Server
# ---------------------------------------------------------------------------


class PeripheralApiV1:
    """
    Version 1 of the peripheral API: events, commands and snapshot for one connected peripheral client set.

    The WebSocket server itself (bind, routing by version) is ``PeripheralAPIServer``, which calls
    ``handle_client()`` for every client that asked for version 1.
    """

    DEFAULT_VOLUME_STEP: float = 0.05
    LATE_ENTITY_RECONNECT_DEBOUNCE_S: float = 1.5
    LATE_ENTITY_RECONNECT_COOLDOWN_S: float = 60.0

    def __init__(self, volume_step: float = DEFAULT_VOLUME_STEP) -> None:
        self._volume_step = volume_step

        self._clients: Set[Any] = set()
        self._state: Optional[ServerState] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None

        # Last conversation exchange — sent in the snapshot to newly-connecting clients
        self._last_stt_text: Optional[str] = None
        self._last_tts_text: Optional[str] = None

        # Current event state — replayed to newly-connecting clients so they can
        # show the correct animation immediately without waiting for the next event.
        # Only "state" events are tracked (pipeline, timer, media, muted, idle).
        # Transient/informational events (stt_text, tts_text, volume_changed, etc.)
        # are not tracked because they carry no ongoing visual state.
        self._current_state: Optional[LVAEvent] = None
        self._current_state_data: Optional[Dict[str, Any]] = None

        # Safeguarded HA reconnect trigger for late entity registrations.
        self._pending_entity_reconnect_task: Optional[asyncio.Task] = None
        self._last_ha_reconnect_at: float = 0.0

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def set_state(self, state: "ServerState") -> None:
        """Attach the shared ``ServerState`` so commands can read/mutate it."""
        self._state = state

    def set_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """Attach the running event loop, used to schedule the late entity reconnect."""
        self._loop = loop

    async def stop(self) -> None:
        """Cancel pending work. The server closes the client connections."""
        if self._pending_entity_reconnect_task is not None:
            self._pending_entity_reconnect_task.cancel()
            self._pending_entity_reconnect_task = None

    # ------------------------------------------------------------------
    # Client handling
    # ------------------------------------------------------------------

    async def handle_client(self, websocket: Any) -> None:
        """Serve one peripheral client until it disconnects."""
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
                    "volume_muted": state.volume == 0.0,
                    "ha_connected": state.connected,
                    "button_controls_locked": state.button_controls_locked,
                    "last_stt_text": self._last_stt_text,
                    "last_tts_text": self._last_tts_text,
                },
            }
        )
        try:
            await websocket.send(payload)
        except Exception:  # pylint: disable=broad-except
            return

        # Replay the current event state so the client immediately shows the
        # right animation — e.g. a timer ticking animation when reconnecting
        # mid-timer, or the muted indicator when reconnecting while muted.
        current_state = self._current_state
        if current_state is None:
            return

        if current_state == LVAEvent.DISCONNECTED and state.connected:
            return

        state_payload: Dict[str, Any] = {"event": current_state.value}
        if self._current_state_data:
            state_payload["data"] = self._current_state_data
        try:
            await websocket.send(json.dumps(state_payload))
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
            # listening, thinking, speaking or wake word active.
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
            self._set_volume(state, satellite, max(0.0, min(1.0, state.volume + delta)))

        elif command == LVACommand.SET_VOLUME:
            data = msg.get("data", {})
            volume = data.get("volume")
            if not isinstance(volume, (int, float)):
                _LOGGER.warning("Peripheral: invalid volume in set_volume command: %s", volume)
                return

            self._set_volume(state, satellite, max(0.0, min(1.0, float(volume))))

        elif command == LVACommand.STOP_TIMER_RINGING:
            if satellite is None:
                return
            if getattr(satellite, "_timer_finished", False):
                satellite._timer_finished = False  # pylint: disable=protected-access
                state.active_wake_words.discard(state.stop_word.id)
                state.tts_player.stop()
                satellite.unduck(owner="timer")
                await self.emit_event(LVAEvent.IDLE)

        elif command in (LVACommand.STOP_MEDIA_PLAYER, LVACommand.PAUSE_MEDIA_PLAYER, LVACommand.RESUME_MEDIA_PLAYER):
            self._control_media_player(state, satellite, LVACommand(command))

        elif command == LVACommand.BUTTON_SINGLE_PRESS:
            if state.button_event_sensor_entity is not None:
                state.button_event_sensor_entity.update_state("single_press")
                if satellite is not None:
                    satellite.send_messages([state.button_event_sensor_entity._get_state_message()])  # pylint: disable=protected-access

        elif command == LVACommand.BUTTON_DOUBLE_PRESS:
            state.tts_player.play(state.button_double_press_sound)
            if state.button_event_sensor_entity is not None:
                state.button_event_sensor_entity.update_state("double_press")
                if satellite is not None:
                    satellite.send_messages([state.button_event_sensor_entity._get_state_message()])  # pylint: disable=protected-access

        elif command == LVACommand.BUTTON_TRIPLE_PRESS:
            state.tts_player.play(state.button_triple_press_sound)
            if state.button_event_sensor_entity is not None:
                state.button_event_sensor_entity.update_state("triple_press")
                if satellite is not None:
                    satellite.send_messages([state.button_event_sensor_entity._get_state_message()])  # pylint: disable=protected-access

        elif command == LVACommand.BUTTON_LONG_PRESS:
            state.tts_player.play(state.button_long_press_sound)
            if state.button_event_sensor_entity is not None:
                state.button_event_sensor_entity.update_state("long_press")
                if satellite is not None:
                    satellite.send_messages([state.button_event_sensor_entity._get_state_message()])  # pylint: disable=protected-access

        elif command == LVACommand.REGISTER_LIGHT:
            self._register_light(msg.get("data") or {}, satellite)

        elif command == LVACommand.REGISTER_BUTTON:
            self._register_button(satellite)

        elif command == LVACommand.REGISTER_BUTTON_LOCK:
            self._register_button_lock(satellite)

    def _register_light(self, data: Dict[str, Any], satellite: Any) -> None:
        """Register a Light declared by a peripheral.

        Idempotent on object_id: repeat registrations (e.g. after a
        peripheral reconnect) keep the existing entity and its state.
        """
        from ..models import LightRegistration  # local import to avoid a cycle

        object_id = str(data.get("object_id", "")).strip()
        if not object_id:
            _LOGGER.warning("register_light without object_id; ignoring")
            return

        state = self._state
        if state is None:
            return

        if any(spec.object_id == object_id for spec in state.pending_lights):
            # Same light already on file; nothing to do.
            return

        spec = LightRegistration(
            name=str(data.get("name", "LEDs")),
            object_id=object_id,
            icon=str(data.get("icon", "mdi:led-strip-variant")),
            effects=[str(e) for e in data.get("effects", []) if e],
            supports_rgb=bool(data.get("supports_rgb", True)),
            supports_brightness=bool(data.get("supports_brightness", True)),
        )
        state.pending_lights.append(spec)
        _LOGGER.info("Light registered: %s (effects=%s)", object_id, spec.effects)

        # If the satellite is already running, materialise the entity
        # now so future messages route correctly. HA only sees it
        # after the integration reconnects, but LVA stays consistent.
        if satellite is not None:
            satellite.register_pending_lights()

        self._schedule_ha_reconnect_for_late_entity("light", object_id)

    def _register_button(self, satellite: Any) -> None:
        """Register button press event support declared by a peripheral.

        Idempotent: repeat registrations from a reconnecting peripheral
        are a no-op — the existing entity and its accumulated event state
        are preserved.

        When the satellite is already running, the entity is materialised
        immediately so subsequent button_* commands route correctly.
        HA only sees the new entity after the integration is reloaded, but
        the same startup-wait window that applies to register_light applies
        here too (see ``--peripheral-startup-wait``).
        """
        state = self._state
        if state is None:
            return

        if state.pending_button:
            # Already registered; keep the existing entity.
            return

        state.pending_button = True
        _LOGGER.info("Button event sensor registered by peripheral")

        if satellite is not None:
            satellite.register_pending_button()

        self._schedule_ha_reconnect_for_late_entity("button", "button_press_event")

    def _register_button_lock(self, satellite: Any) -> None:
        """Register the button-lock switch declared by a peripheral.

        Idempotent: repeat registrations from a reconnecting peripheral
        are a no-op — the existing entity and its current lock state
        are preserved.

        When the satellite is already running, the entity is materialised
        immediately so a subsequent switch toggle routes correctly. HA
        only sees the new entity after the integration is reloaded, but
        the same startup-wait window that applies to register_light
        applies here too (see --peripheral-startup-wait).
        """
        state = self._state
        if state is None:
            return

        if state.pending_button_lock:
            # Already registered; keep the existing entity and its state.
            return

        state.pending_button_lock = True
        _LOGGER.info("Button lock switch registered by peripheral")

        if satellite is not None:
            satellite.register_pending_button_lock()

        self._schedule_ha_reconnect_for_late_entity("button_lock", "disable_button_controls")

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _schedule_ha_reconnect_for_late_entity(self, entity_kind: str, entity_id: str) -> None:
        """Reconnect HA once for late peripheral entity registrations."""
        state = self._state
        if state is None or not state.connected or state.satellite is None:
            return

        if self._loop is None:
            return

        if self._pending_entity_reconnect_task is not None and not self._pending_entity_reconnect_task.done():
            _LOGGER.debug(
                "Coalescing late %s registration for '%s' into pending HA reconnect",
                entity_kind,
                entity_id,
            )
            return

        async def _trigger() -> None:
            try:
                await asyncio.sleep(self.LATE_ENTITY_RECONNECT_DEBOUNCE_S)

                state_now = self._state
                if state_now is None or not state_now.connected:
                    return

                satellite_now = state_now.satellite
                if satellite_now is None:
                    return

                now = time.monotonic()
                if now - self._last_ha_reconnect_at < self.LATE_ENTITY_RECONNECT_COOLDOWN_S:
                    _LOGGER.info(
                        "Skipping HA reconnect after late %s '%s': cooldown active",
                        entity_kind,
                        entity_id,
                    )
                    return

                transport = getattr(satellite_now, "_transport", None)
                if transport is None:
                    return

                self._last_ha_reconnect_at = now
                _LOGGER.info(
                    "Late %s '%s' registered after HA handshake; forcing one HA reconnect",
                    entity_kind,
                    entity_id,
                )
                transport.close()
            except asyncio.CancelledError:
                return

        self._pending_entity_reconnect_task = self._loop.create_task(_trigger())

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

        # ----------------------------------------------------------------
        # Track current "state" so newly-connecting clients receive it on
        # connect via _send_snapshot.  Only persistent/visual states are
        # stored — transient informational events are skipped.
        # ----------------------------------------------------------------
        _STATE_EVENTS = {
            LVAEvent.WAKE_WORD_DETECTED,
            LVAEvent.LISTENING,
            LVAEvent.THINKING,
            LVAEvent.TTS_SPEAKING,
            LVAEvent.TTS_FINISHED,
            LVAEvent.IDLE,
            LVAEvent.MUTED,
            LVAEvent.TIMER_TICKING,
            LVAEvent.TIMER_RINGING,
            LVAEvent.MEDIA_PLAYER_PLAYING,
            LVAEvent.DISCONNECTED,
            LVAEvent.PIPELINE_ERROR,
            LVAEvent.VOLUME_MUTED,
        }
        if event in _STATE_EVENTS:
            self._current_state = event
            self._current_state_data = data or None
        elif event == LVAEvent.TIMER_UPDATED and self._current_state == LVAEvent.TIMER_TICKING:
            # Keep state as TIMER_TICKING but refresh the countdown data
            self._current_state_data = data or None

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

    def _set_volume(self, state: "ServerState", satellite: Any, volume: float) -> None:
        """
        Set the volume on behalf of a peripheral. It goes through the media player entity, like a volume set in Home Assistant.
        :param state: The server state.
        :param satellite: The satellite that Home Assistant is connected to, None if there is none.
        :param volume: Volume 0.0 to 1.0.
        """
        entity = state.media_player_entity
        if entity is None:
            volume_percent = int(round(volume * 100))
            state.music_player.set_volume(volume_percent)
            state.tts_player.set_volume(volume_percent)
            # persist_volume also emits VOLUME_CHANGED via models.py
            state.persist_volume(volume)
            return

        entity.set_volume(volume)
        if satellite is not None:
            # Push the new volume to HA so its media player entity updates in real time
            satellite.send_messages([MediaPlayerStateResponse(key=entity.key, state=entity.state, volume=volume, muted=entity.muted)])

    def _control_media_player(self, state: "ServerState", satellite: Any, command: LVACommand) -> None:
        """
        Stop, pause or resume the music on behalf of a peripheral. It goes through the media player entity, like the same command from Home Assistant.
        :param state: The server state.
        :param satellite: The satellite that Home Assistant is connected to, None if there is none.
        :param command: ``stop_media_player``, ``pause_media_player`` or ``resume_media_player``.
        """
        entity = state.media_player_entity
        if entity is None:
            {
                LVACommand.STOP_MEDIA_PLAYER: state.music_player.stop,
                LVACommand.PAUSE_MEDIA_PLAYER: state.music_player.pause,
                LVACommand.RESUME_MEDIA_PLAYER: state.music_player.resume,
            }[command]()
            return

        response = {
            LVACommand.STOP_MEDIA_PLAYER: entity.stop,
            LVACommand.PAUSE_MEDIA_PLAYER: entity.pause,
            LVACommand.RESUME_MEDIA_PLAYER: entity.resume,
        }[command]()
        if satellite is not None:
            satellite.send_messages([response])
