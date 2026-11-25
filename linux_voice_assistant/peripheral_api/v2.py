"""WebSocket peripheral API, version 2.

The reduced API for the Linux Peripherals Controller: the status of the assistant and a few commands. LVA creates no Home
Assistant entities for a version 2 client, except the Hardware Mute switch. The specification is ``docs/peripheral_api/v2.md``.

Events  (LVA → peripheral): {"event": "<name>", "data": {...}}
Commands (peripheral → LVA): {"command": "<name>", "data": {...}}

Events: hello, lva_status, state, mute_status, volume_status, timer_status, timer_remaining_time, timer_remaining_percentage,
media_player_status, pipeline_error, timer_auto_stopped, hardware_mute_status, hardware_mute_possible, hardware_mute_on,
hardware_mute_off.
Commands: start_listening, stop, mute_on, mute_off, volume_up, volume_down, volume_set, timer_stop, button_pressed,
hardware_mute_status, hardware_mute_possible.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Set

from aioesphomeapi.api_pb2 import SwitchStateResponse  # type: ignore[attr-defined]  # pylint: disable=no-name-in-module

from .late_entity_reconnect import LateEntityReconnect
from .v2_status import StatusInputs, V2Status, derive_status, pipeline_state_after, status_messages
from .v2_timers import TimerInfo, summarize_timers, update_timers
from .versions import SUPPORTED_VERSIONS

if TYPE_CHECKING:
    from ..models import ServerState
    from .v1 import LVAEvent

_LOGGER = logging.getLogger(__name__)

_SOUNDS_DIR = Path(__file__).resolve().parents[2] / "sounds"
_VOLUME_LIMIT_SOUND = _SOUNDS_DIR / "volume_limit.wav"
_VOLUME_STEP_SOUND = _SOUNDS_DIR / "volume_step.wav"
_MEDIA_STATE_NAMES = {"IDLE": "idle", "PLAYING": "playing", "PAUSED": "pause"}


class PeripheralApiV2:
    """
    Version 2 of the peripheral API for the clients that connected to ``/v2``.

    The server calls ``handle_client()`` for every such client and ``emit_event()`` for every event of LVA. The status that is
    sent comes from the state of LVA, see ``v2_status.py``.
    """

    DEFAULT_VOLUME_STEP: float = 0.05
    VALID_PRESSES = ("single", "double", "triple", "long")

    def __init__(self, volume_step: float = DEFAULT_VOLUME_STEP) -> None:
        self._volume_step = volume_step
        self._clients: Set[Any] = set()
        self._state: Optional[ServerState] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._pipeline_state = "idle"
        self._timers: Dict[str, TimerInfo] = {}
        self._status: Optional[V2Status] = None
        self._ticker: Optional[asyncio.Task] = None
        self._late_reconnect = LateEntityReconnect()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def set_state(self, state: "ServerState") -> None:
        """Attach the shared ``ServerState`` the status is worked out from and the commands change."""
        self._state = state

    def set_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """Attach the running event loop."""
        self._loop = loop

    async def stop(self) -> None:
        """Cancel the countdown."""
        if self._ticker is not None:
            self._ticker.cancel()
            self._ticker = None
        self._late_reconnect.cancel()

    # ------------------------------------------------------------------
    # Client handling and events
    # ------------------------------------------------------------------

    async def handle_client(self, websocket: Any) -> None:
        """Serve one version 2 client until it disconnects."""
        addr = getattr(websocket, "remote_address", "unknown")
        self._clients.add(websocket)
        _LOGGER.info("Peripheral client connected (API v2): %s", addr)

        try:
            await websocket.send(json.dumps({"event": "hello", "data": {"api_version": "v2", "supported_versions": list(SUPPORTED_VERSIONS)}}))
            for message in status_messages(None, self._current_status()) + self._hardware_mute_messages():
                await websocket.send(json.dumps(message))
            async for raw in websocket:
                await self._dispatch_command(raw, websocket)
        except Exception as exc:  # pylint: disable=broad-except
            _LOGGER.debug("Peripheral client %s error: %s", addr, exc)
        finally:
            self._clients.discard(websocket)
            _LOGGER.info("Peripheral client disconnected (API v2): %s", addr)

    async def emit_event(self, event: "LVAEvent", data: Optional[Dict[str, Any]] = None) -> None:
        """
        Take an event of LVA into account and tell the clients what changed.
        :param event: The event of LVA.
        :param data: Data of the event.
        """
        self._pipeline_state = pipeline_state_after(event.value, self._pipeline_state)
        self._timers = update_timers(self._timers, event.value, data, time.monotonic())
        if event.value == "pipeline_error":
            await self._broadcast([{"event": "pipeline_error", "data": {"reason": str((data or {}).get("reason", "unknown"))}}])
        elif event.value == "timer_auto_stopped":
            await self._broadcast([{"event": "timer_auto_stopped", "data": {"seconds": int((data or {}).get("seconds", 0))}}])
        await self.refresh_status()

    async def refresh_status(self) -> None:
        """Work out the status again and send what changed."""
        if self._state is None:
            return
        current = self._current_status()
        messages = status_messages(self._status, current)
        self._status = current
        if messages:
            _LOGGER.debug("Peripheral API v2 status: %s", ", ".join(f"{m['event']}={next(iter(m['data'].values()))}" for m in messages))
            await self._broadcast(messages)
        self._keep_ticker_running()

    async def request_hardware_mute(self, muted: bool) -> None:
        """
        Ask the peripherals to mute or unmute the hardware of the microphone, because the Hardware Mute switch was changed.
        :param muted: True to mute the hardware.
        The request is only sent if a peripheral has said that the hardware mute is possible and one is connected. Otherwise it is
        ignored, and Home Assistant is told the state again so that the switch jumps back.
        """
        state = self._state
        if state is None:
            return
        if state.hardware_mute_possible is not True or not self._clients:
            _LOGGER.debug(
                "Ignoring the request to %s the hardware mute: possible=%s, %d peripheral(s) connected",
                "set" if muted else "clear",
                state.hardware_mute_possible,
                len(self._clients),
            )
            self._tell_home_assistant_hardware_mute()
            return
        _LOGGER.debug("Asking the peripherals to %s the hardware mute", "set" if muted else "clear")
        await self._broadcast([{"event": "hardware_mute_on" if muted else "hardware_mute_off"}])

    # ------------------------------------------------------------------
    # Commands
    # ------------------------------------------------------------------

    async def _dispatch_command(self, raw: str, sender: Any = None) -> None:
        """Parse and execute a JSON command from the peripheral. ``sender`` is the client it came from."""
        try:
            msg = json.loads(raw)
        except json.JSONDecodeError:
            _LOGGER.warning("Peripheral (API v2): invalid JSON: %.200s", raw)
            return

        command = msg.get("command", "") if isinstance(msg, dict) else ""
        data = msg.get("data") if isinstance(msg, dict) else None
        state = self._state
        if not command or state is None:
            return

        _LOGGER.debug("Peripheral (API v2) command received: %s", command)
        satellite = state.satellite

        if command == "start_listening":
            if satellite is not None and not state.muted:
                satellite.start_listening()
                # It ends a ringing timer without an event, so the status is worked out again right away
                await self.refresh_status()
        elif command == "stop":
            if satellite is not None and not satellite.timer_ringing:
                satellite.stop()
        elif command == "timer_stop":
            if satellite is not None and satellite.timer_ringing:
                satellite.stop()
        elif command in ("mute_on", "mute_off"):
            self._set_mic_muted(state, satellite, command == "mute_on")
        elif command in ("volume_up", "volume_down"):
            self._change_volume(state, satellite, self._volume_step if command == "volume_up" else -self._volume_step)
        elif command == "volume_set":
            self._set_volume_command(state, satellite, data)
        elif command == "button_pressed":
            self._play_button_sound(state, data)
        elif command == "hardware_mute_status":
            await self._set_hardware_muted(state, data, sender)
        elif command == "hardware_mute_possible":
            await self._set_hardware_mute_possible(state, data, sender)
        else:
            _LOGGER.warning("Peripheral (API v2): unknown command '%s'", command)

    async def _set_hardware_muted(self, state: "ServerState", data: Any, sender: Any) -> None:
        """Take over the hardware mute state that a peripheral reported. LVA's own mute is not changed."""
        status = data.get("status") if isinstance(data, dict) else None
        if status not in ("muted", "unmuted"):
            _LOGGER.warning("Peripheral (API v2): invalid status in hardware_mute_status command: %s", status)
            return
        muted = status == "muted"
        if state.hardware_muted == muted:
            _LOGGER.debug("Hardware mute is already reported as %s", status)
            return
        _LOGGER.debug("Hardware mute reported: %s", status)
        state.hardware_muted = muted
        self._register_hardware_mute(state)
        self._tell_home_assistant_hardware_mute()
        await self._broadcast([{"event": "hardware_mute_status", "data": {"status": status}}], exclude=sender)

    async def _set_hardware_mute_possible(self, state: "ServerState", data: Any, sender: Any) -> None:
        """Take over whether a peripheral can mute the hardware."""
        possible = data.get("possible") if isinstance(data, dict) else None
        if not isinstance(possible, bool):
            _LOGGER.warning("Peripheral (API v2): invalid possible in hardware_mute_possible command: %s", possible)
            return
        if state.hardware_mute_possible == possible:
            _LOGGER.debug("Hardware mute possible is already reported as %s", possible)
            return
        _LOGGER.debug("Hardware mute possible reported: %s", possible)
        state.hardware_mute_possible = possible
        self._register_hardware_mute(state)
        await self._broadcast([{"event": "hardware_mute_possible", "data": {"possible": possible}}], exclude=sender)

    def _set_mic_muted(self, state: "ServerState", satellite: Any, muted: bool) -> None:
        """Mute or unmute the microphone like the Mute switch does, and tell Home Assistant."""
        if satellite is None or state.muted == muted:
            return
        satellite._set_muted(muted)  # pylint: disable=protected-access
        entity = state.mute_switch_entity
        if entity is not None:
            entity._switch_state = muted  # pylint: disable=protected-access
            satellite.send_messages([SwitchStateResponse(key=entity.key, state=muted)])

    def _set_volume_command(self, state: "ServerState", satellite: Any, data: Any) -> None:
        volume = data.get("volume") if isinstance(data, dict) else None
        if isinstance(volume, bool) or not isinstance(volume, (int, float)):
            _LOGGER.warning("Peripheral (API v2): invalid volume in volume_set command: %s", volume)
            return
        self._apply_volume(state, satellite, max(0.0, min(1.0, float(volume) / 100)))

    def _change_volume(self, state: "ServerState", satellite: Any, delta: float) -> None:
        volume = max(0.0, min(1.0, state.volume + delta))
        if volume == state.volume:
            _LOGGER.debug("Volume is at the limit (%d %%), playing the limit sound", round(volume * 100))
            state.tts_player.play(str(_VOLUME_LIMIT_SOUND))
            return
        _LOGGER.debug("Volume %d %% -> %d %%, playing the step sound", round(state.volume * 100), round(volume * 100))
        self._apply_volume(state, satellite, volume)
        state.tts_player.play(str(_VOLUME_STEP_SOUND))

    def _apply_volume(self, state: "ServerState", satellite: Any, volume: float) -> None:
        """Set the volume through the media player entity, like a volume set in Home Assistant."""
        entity = state.media_player_entity
        if entity is None:
            state.music_player.set_volume(int(round(volume * 100)))
            state.tts_player.set_volume(int(round(volume * 100)))
            state.persist_volume(volume)
            return
        entity.set_volume(volume)
        if satellite is not None:
            satellite.send_messages([entity._get_state_message()])  # pylint: disable=protected-access

    def _play_button_sound(self, state: "ServerState", data: Any) -> None:
        press = data.get("press") if isinstance(data, dict) else None
        if press not in self.VALID_PRESSES:
            _LOGGER.warning("Peripheral (API v2): invalid press in button_pressed command: %s", press)
            return
        sound = {"double": state.button_double_press_sound, "triple": state.button_triple_press_sound, "long": state.button_long_press_sound}.get(press)
        if sound:
            state.tts_player.play(sound)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _current_status(self) -> V2Status:
        """Work out the status from the state of LVA."""
        state = self._state
        now = time.monotonic()
        if state is None:
            return derive_status(
                StatusInputs(False, False, 0.0, False, self._pipeline_state, "idle", summarize_timers(self._timers, now, False)),
                self._status,
            )

        satellite = state.satellite
        entity = state.media_player_entity
        ringing = bool(satellite is not None and satellite.timer_ringing)
        announcing = bool((satellite is not None and satellite.announcing) or (entity is not None and entity.announcing))
        media_state = _MEDIA_STATE_NAMES.get(entity.state.name, "idle") if entity is not None else "idle"
        inputs = StatusInputs(
            connected=state.connected,
            muted=state.muted,
            volume=state.volume,
            announcing=announcing,
            pipeline_state=self._pipeline_state,
            media_state=media_state,
            timer=summarize_timers(self._timers, now, ringing),
        )
        return derive_status(inputs, self._status)

    def _register_hardware_mute(self, state: "ServerState") -> None:
        """
        Add the Hardware Mute switch the first time a peripheral reports a hardware mute value.

        Home Assistant only sees an entity that appears later after it connected again, so it is reconnected once.
        """
        if state.pending_hardware_mute:
            return
        state.pending_hardware_mute = True
        _LOGGER.info("Hardware mute switch registered by peripheral")
        if state.satellite is not None:
            state.satellite.register_pending_hardware_mute()
        self._late_reconnect.schedule(state, self._loop, "hardware_mute", "hardware_mute")

    def _hardware_mute_messages(self) -> List[Dict[str, Any]]:
        """The hardware mute values that a peripheral reported, for a client that connects."""
        state = self._state
        if state is None:
            return []
        messages: List[Dict[str, Any]] = []
        if state.hardware_muted is not None:
            messages.append({"event": "hardware_mute_status", "data": {"status": "muted" if state.hardware_muted else "unmuted"}})
        if state.hardware_mute_possible is not None:
            messages.append({"event": "hardware_mute_possible", "data": {"possible": state.hardware_mute_possible}})
        return messages

    def _tell_home_assistant_hardware_mute(self) -> None:
        """Tell every Home Assistant client the hardware mute state, so the Hardware Mute switch shows it."""
        state = self._state
        entity = state.hardware_mute_switch_entity if state is not None else None
        if state is not None and entity is not None:
            state.broadcast([entity.state_message()])

    async def _broadcast(self, messages: List[Dict[str, Any]], exclude: Any = None) -> None:
        for ws in list(self._clients):
            if ws is exclude:
                continue
            try:
                for message in messages:
                    await ws.send(json.dumps(message))
            except Exception:  # pylint: disable=broad-except
                self._clients.discard(ws)

    def _keep_ticker_running(self) -> None:
        """Count the time left of the timers down once a second while one counts down."""
        if not self._timers or (self._ticker is not None and not self._ticker.done()) or self._loop is None:
            return

        async def _tick() -> None:
            while self._timers:
                await asyncio.sleep(1.0)
                await self.refresh_status()

        self._ticker = self._loop.create_task(_tick())
