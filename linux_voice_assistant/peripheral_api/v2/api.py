"""WebSocket peripheral API, version 2.

The reduced API for the Linux Peripherals Controller: the status of the assistant and a few commands. LVA creates no Home
Assistant entities for a version 2 client. The specification is ``docs/peripheral_api/v2.md``.

Event   (reports something): {"event": "<name>", "data": {...}}
Command (asks for something): {"command": "<name>", "data": {...}}
Which one a message is does not depend on the direction.

Events from LVA: hello, lva_status, state, mute_status, volume_status, timer_status, timer_remaining_time, timer_remaining_percentage,
timer_full_data_set, media_player_status.
Commands from LVA: hardware_muted_on, hardware_muted_off.
Commands from the peripheral: listening_start, listening_stop, muted_on, muted_off, volume_up, volume_down, volume_set, volume_silent, timer_stop,
button_pressed.
Events from the peripheral: hardware_mute_status, hardware_mute_type.
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Set

from ..versions import SUPPORTED_VERSIONS
from .status import StatusInputs, V2Status, derive_status, pipeline_state_after, status_messages
from .timers import TimerSummary, has_running_timer, summarize_timers

if TYPE_CHECKING:
    from ...models import ServerState
    from ..v1 import LVAEvent

_LOGGER = logging.getLogger(__name__)

_SOUNDS_DIR = Path(__file__).resolve().parents[3] / "sounds"
_VOLUME_LIMIT_SOUND = _SOUNDS_DIR / "volume_limit.wav"
_VOLUME_STEP_SOUND = _SOUNDS_DIR / "volume_step.wav"
HARDWARE_MUTE_TYPES = ("none", "changeable", "read_only")
_MEDIA_STATE_NAMES = {"IDLE": "idle", "PLAYING": "playing", "PAUSED": "pause"}


class PeripheralApiV2:
    """
    Version 2 of the peripheral API for the clients that connected to ``/v2``.

    The server calls ``handle_client()`` for every such client and ``emit_event()`` for every event of LVA. The status that is
    sent comes from the state of LVA, see ``status.py``.
    """

    DEFAULT_VOLUME_STEP: float = 0.05
    VALID_PRESSES = ("single", "double", "triple", "long")

    def __init__(self, volume_step: float = DEFAULT_VOLUME_STEP) -> None:
        self._volume_step = volume_step
        self._clients: Set[Any] = set()
        self._state: Optional[ServerState] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._pipeline_state = "idle"
        self._status: Optional[V2Status] = None
        self._ticker: Optional[asyncio.Task] = None
        self._timer_auto_stopped = False

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
            for message in status_messages(None, self._current_status()):
                await websocket.send(json.dumps(message))
            async for raw in websocket:
                await self._dispatch_command(raw)
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
        if event.value == "timer_auto_stopped":
            # Several timers can ring: only the one whose end left none ringing is reported as stopped on its own
            self._timer_auto_stopped = not self._ringing()
        await self.refresh_status()
        if self._pipeline_state == "pipeline_error":
            self._pipeline_state = "idle"
            await self.refresh_status()

    async def refresh_status(self) -> None:
        """Work out the status again and send what changed."""
        if self._state is None:
            return
        current = self._current_status()
        if current.timer_status != "ringing":
            self._timer_auto_stopped = False
        messages = status_messages(self._status, current)
        self._status = current
        if messages:
            _LOGGER.debug("Peripheral API v2 status: %s", ", ".join(f"{m['event']}={next(iter(m['data'].values()))}" for m in messages))
            await self._broadcast(messages)
        self._keep_ticker_running()

    async def request_hardware_mute(self, muted: bool) -> None:
        """
        Ask the peripherals to mute or unmute the hardware of the microphone.
        :param muted: True to mute the hardware.
        The request is only sent if a peripheral has said that the hardware mute is ``changeable`` and one is connected. Otherwise it
        is ignored.
        """
        state = self._state
        if state is None:
            return
        if state.hardware_mute_type != "changeable" or not self._clients:
            _LOGGER.debug(
                "Ignoring the request to %s the hardware mute: type=%s, %d peripheral(s) connected",
                "set" if muted else "clear",
                state.hardware_mute_type,
                len(self._clients),
            )
            return
        _LOGGER.debug("Asking the peripherals to %s the hardware mute", "set" if muted else "clear")
        await self._broadcast([{"command": "hardware_muted_on" if muted else "hardware_muted_off"}])

    # ------------------------------------------------------------------
    # Commands
    # ------------------------------------------------------------------

    async def _dispatch_command(self, raw: str) -> None:
        """Parse and execute a JSON message from the peripheral: a command to execute or an event that reports the hardware mute."""
        try:
            msg = json.loads(raw)
        except json.JSONDecodeError:
            _LOGGER.warning("Peripheral (API v2): invalid JSON: %.200s", raw)
            return

        event = msg.get("event", "") if isinstance(msg, dict) else ""
        command = msg.get("command", "") if isinstance(msg, dict) else ""
        data = msg.get("data") if isinstance(msg, dict) else None
        state = self._state
        if not (command or event) or state is None:
            return

        if event:
            _LOGGER.debug("Peripheral (API v2) event received: %s", event)
            self._handle_peripheral_event(state, event, data)
            return

        _LOGGER.debug("Peripheral (API v2) command received: %s", command)
        satellite = state.satellite

        if command == "listening_start":
            if not state.muted:
                if satellite is not None:
                    satellite.start_listening()
                else:
                    state.timer_alarm.stop_ringing()
                # It ends a ringing timer without an event, so the status is worked out again right away
                await self.refresh_status()
        elif command == "listening_stop":
            if satellite is not None and not state.timer_alarm.ringing:
                satellite.stop()
        elif command == "timer_stop":
            state.timer_alarm.stop_ringing()
        elif command in ("muted_on", "muted_off"):
            self._set_mic_muted(state, satellite, command == "muted_on")
        elif command in ("volume_up", "volume_down"):
            self._change_volume(state, satellite, self._volume_step if command == "volume_up" else -self._volume_step)
        elif command == "volume_set":
            self._set_volume_command(state, satellite, data)
        elif command == "volume_silent":
            self._apply_volume(state, satellite, 0.0)
        elif command == "button_pressed":
            self._play_button_sound(state, data)
        else:
            _LOGGER.warning("Peripheral (API v2): unknown command '%s'", command)

    def _handle_peripheral_event(self, state: "ServerState", event: str, data: Any) -> None:
        """Keep what a peripheral reports about its hardware mute."""
        if event == "hardware_mute_status":
            self._set_hardware_muted(state, data)
        elif event == "hardware_mute_type":
            self._set_hardware_mute_type(state, data)
        else:
            _LOGGER.warning("Peripheral (API v2): unknown event '%s'", event)

    def _set_hardware_muted(self, state: "ServerState", data: Any) -> None:
        """Keep the hardware mute state that a peripheral reported. LVA's own mute is not changed."""
        status = data.get("status") if isinstance(data, dict) else None
        if status not in ("muted", "unmuted"):
            _LOGGER.warning("Peripheral (API v2): invalid status in hardware_mute_status event: %s", status)
            return
        muted = status == "muted"
        if state.hardware_muted == muted:
            _LOGGER.debug("Hardware mute is already reported as %s", status)
            return
        _LOGGER.debug("Hardware mute reported: %s", status)
        state.hardware_muted = muted

    def _set_hardware_mute_type(self, state: "ServerState", data: Any) -> None:
        """Keep the kind of hardware mute that a peripheral reported."""
        mute_type = data.get("type") if isinstance(data, dict) else None
        if mute_type not in HARDWARE_MUTE_TYPES:
            _LOGGER.warning("Peripheral (API v2): invalid type in hardware_mute_type event: %s", mute_type)
            return
        if state.hardware_mute_type == mute_type:
            _LOGGER.debug("Hardware mute type is already reported as %s", mute_type)
            return
        _LOGGER.debug("Hardware mute type reported: %s", mute_type)
        state.hardware_mute_type = mute_type

    def _set_mic_muted(self, state: "ServerState", satellite: Any, muted: bool) -> None:
        """
        Mute or unmute the microphone like the Mute switch does, and tell Home Assistant.

        A peripheral may also unmute while ``--enable-remote-unmute`` is off, because only Home Assistant is kept from unmuting then.
        """
        if satellite is None or state.muted == muted:
            return
        satellite._set_muted(muted)  # pylint: disable=protected-access
        entity = state.mute_switch_entity
        if entity is not None:
            entity._switch_state = muted  # pylint: disable=protected-access
            satellite.send_messages([entity.state_message()])

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
        if state is None:
            return derive_status(StatusInputs(False, False, 0.0, False, self._pipeline_state, "idle", TimerSummary("idle", 0, 0)), self._status)

        satellite = state.satellite
        entity = state.media_player_entity
        announcing = bool((satellite is not None and satellite.announcing) or (entity is not None and entity.announcing))
        media_state = _MEDIA_STATE_NAMES.get(entity.state.name, "idle") if entity is not None else "idle"
        inputs = StatusInputs(
            connected=state.connected,
            muted=state.muted,
            volume=state.volume,
            announcing=announcing,
            pipeline_state=self._pipeline_state,
            media_state=media_state,
            timer=summarize_timers(state.timers.timers, state.timer_alarm.now()),
            timer_auto_stopped=self._timer_auto_stopped,
        )
        return derive_status(inputs, self._status)

    def _ringing(self) -> bool:
        return bool(self._state is not None and self._state.timer_alarm.ringing)

    async def _broadcast(self, messages: List[Dict[str, Any]]) -> None:
        for ws in list(self._clients):
            try:
                for message in messages:
                    await ws.send(json.dumps(message))
            except Exception:  # pylint: disable=broad-except
                self._clients.discard(ws)

    def _keep_ticker_running(self) -> None:
        """Count the time left of the timers down once a second while one counts down."""
        state = self._state
        if state is None or not has_running_timer(state.timers.timers) or (self._ticker is not None and not self._ticker.done()) or self._loop is None:
            return

        async def _tick() -> None:
            while has_running_timer(state.timers.timers):
                await asyncio.sleep(1.0)
                await self.refresh_status()

        self._ticker = self._loop.create_task(_tick())
