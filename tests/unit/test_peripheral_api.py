"""Unit tests for the peripheral WebSocket API (PeripheralAPIServer)."""

import asyncio
import json
from typing import Any, Dict, Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from linux_voice_assistant.peripheral_api import LVACommand, LVAEvent, PeripheralAPIServer
from tests.unit.conftest import make_state

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_server(state=None) -> PeripheralAPIServer:
    """Return a PeripheralAPIServer, optionally attached to a ServerState."""
    server = PeripheralAPIServer()
    if state is not None:
        server.set_state(state)
    return server


def make_media_entity(key: int = 1, volume: float = 0.5, muted: bool = False):
    entity = MagicMock()
    entity.key = key
    entity.volume = volume
    entity.muted = muted
    entity.previous_volume = volume
    return entity


async def dispatch(server: PeripheralAPIServer, command: str, data: Optional[dict] = None) -> None:
    payload: Dict[str, Any] = {"command": command}
    if data is not None:
        payload["data"] = data
    await server._dispatch_command(json.dumps(payload))  # pylint: disable=protected-access


# ---------------------------------------------------------------------------
# __init__ / set_state
# ---------------------------------------------------------------------------


class TestInit:
    def test_default_host_and_port(self):
        server = PeripheralAPIServer()
        assert server._host == "0.0.0.0"
        assert server._port == 6055

    def test_custom_host_and_port(self):
        server = PeripheralAPIServer(host="127.0.0.1", port=9999)
        assert server._host == "127.0.0.1"
        assert server._port == 9999

    def test_default_volume_step(self):
        server = PeripheralAPIServer()
        assert server._volume_step == PeripheralAPIServer.DEFAULT_VOLUME_STEP

    def test_custom_volume_step(self):
        server = PeripheralAPIServer(volume_step=0.1)
        assert server._volume_step == pytest.approx(0.1)

    def test_no_clients_on_init(self):
        server = PeripheralAPIServer()
        assert server._clients == set()

    def test_no_state_on_init(self):
        server = PeripheralAPIServer()
        assert server._state is None

    def test_no_current_state_on_init(self):
        server = PeripheralAPIServer()
        assert server._current_state is None


class TestSetState:
    def test_attaches_state(self, tmp_path):
        state = make_state(tmp_path)
        server = make_server()
        server.set_state(state)
        assert server._state is state


# ---------------------------------------------------------------------------
# start() / stop()
# ---------------------------------------------------------------------------


class TestStartStop:
    @pytest.mark.asyncio
    async def test_start_creates_server(self):
        server = make_server()
        fake_server = MagicMock()

        with patch("websockets.server.serve", AsyncMock(return_value=fake_server)) as mock_serve:
            await server.start()

        mock_serve.assert_called_once()
        assert server._server is fake_server

    @pytest.mark.asyncio
    async def test_start_uses_configured_host_and_port(self):
        server = make_server()
        server._host = "127.0.0.1"
        server._port = 1234

        with patch("websockets.server.serve", AsyncMock(return_value=MagicMock())) as mock_serve:
            await server.start()

        _, args, kwargs = mock_serve.mock_calls[0]
        assert "127.0.0.1" in args or kwargs.get("host") == "127.0.0.1"

    @pytest.mark.asyncio
    async def test_start_sets_loop(self):
        server = make_server()

        with patch("websockets.server.serve", AsyncMock(return_value=MagicMock())):
            await server.start()

        assert server._loop is asyncio.get_running_loop()

    @pytest.mark.asyncio
    async def test_stop_closes_server(self):
        server = make_server()
        fake_server = MagicMock()
        fake_server.wait_closed = AsyncMock()
        server._server = fake_server

        await server.stop()

        fake_server.close.assert_called_once()
        fake_server.wait_closed.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_stop_without_server_does_not_raise(self):
        server = make_server()
        await server.stop()  # should not raise

    @pytest.mark.asyncio
    async def test_stop_cancels_pending_reconnect_task(self):
        server = make_server()
        pending_task = MagicMock()
        server._pending_entity_reconnect_task = pending_task

        await server.stop()

        pending_task.cancel.assert_called_once()
        assert server._pending_entity_reconnect_task is None


# ---------------------------------------------------------------------------
# _send_snapshot()
# ---------------------------------------------------------------------------


class TestSendSnapshot:
    @pytest.mark.asyncio
    async def test_no_state_does_nothing(self):
        server = make_server()
        websocket = AsyncMock()
        await server._send_snapshot(websocket)  # pylint: disable=protected-access
        websocket.send.assert_not_called()

    @pytest.mark.asyncio
    async def test_sends_snapshot_payload(self, tmp_path):
        state = make_state(tmp_path, volume=0.75, muted=False, connected=True)
        server = make_server(state)
        websocket = AsyncMock()

        await server._send_snapshot(websocket)  # pylint: disable=protected-access

        sent = json.loads(websocket.send.call_args_list[0].args[0])
        assert sent["event"] == "snapshot"
        assert sent["data"]["volume"] == pytest.approx(0.75)
        assert sent["data"]["muted"] is False
        assert sent["data"]["ha_connected"] is True

    @pytest.mark.asyncio
    async def test_snapshot_includes_last_conversation_text(self, tmp_path):
        state = make_state(tmp_path)
        server = make_server(state)
        server._last_stt_text = "turn on the lights"  # pylint: disable=protected-access
        server._last_tts_text = "OK"  # pylint: disable=protected-access
        websocket = AsyncMock()

        await server._send_snapshot(websocket)  # pylint: disable=protected-access

        sent = json.loads(websocket.send.call_args_list[0].args[0])
        assert sent["data"]["last_stt_text"] == "turn on the lights"
        assert sent["data"]["last_tts_text"] == "OK"

    @pytest.mark.asyncio
    async def test_no_current_state_skips_replay(self, tmp_path):
        state = make_state(tmp_path)
        server = make_server(state)
        websocket = AsyncMock()

        await server._send_snapshot(websocket)  # pylint: disable=protected-access

        assert websocket.send.await_count == 1

    @pytest.mark.asyncio
    async def test_replays_current_state(self, tmp_path):
        state = make_state(tmp_path)
        server = make_server(state)
        server._current_state = LVAEvent.THINKING  # pylint: disable=protected-access
        websocket = AsyncMock()

        await server._send_snapshot(websocket)  # pylint: disable=protected-access

        assert websocket.send.await_count == 2
        replayed = json.loads(websocket.send.call_args_list[1].args[0])
        assert replayed["event"] == "thinking"

    @pytest.mark.asyncio
    async def test_replays_current_state_with_data(self, tmp_path):
        state = make_state(tmp_path)
        server = make_server(state)
        server._current_state = LVAEvent.TIMER_TICKING  # pylint: disable=protected-access
        server._current_state_data = {"id": "1", "seconds_left": 30}  # pylint: disable=protected-access
        websocket = AsyncMock()

        await server._send_snapshot(websocket)  # pylint: disable=protected-access

        replayed = json.loads(websocket.send.call_args_list[1].args[0])
        assert replayed["data"]["seconds_left"] == 30

    @pytest.mark.asyncio
    async def test_skips_replay_of_disconnected_when_connected(self, tmp_path):
        state = make_state(tmp_path, connected=True)
        server = make_server(state)
        server._current_state = LVAEvent.DISCONNECTED  # pylint: disable=protected-access
        websocket = AsyncMock()

        await server._send_snapshot(websocket)  # pylint: disable=protected-access

        # Only the snapshot itself should have been sent
        assert websocket.send.await_count == 1


# ---------------------------------------------------------------------------
# _dispatch_command() — basic parsing
# ---------------------------------------------------------------------------


class TestDispatchCommandParsing:
    @pytest.mark.asyncio
    async def test_invalid_json_does_not_raise(self, tmp_path):
        state = make_state(tmp_path)
        server = make_server(state)
        await server._dispatch_command("not json")  # pylint: disable=protected-access

    @pytest.mark.asyncio
    async def test_missing_command_is_noop(self, tmp_path):
        state = make_state(tmp_path)
        server = make_server(state)
        await server._dispatch_command(json.dumps({}))  # pylint: disable=protected-access

    @pytest.mark.asyncio
    async def test_no_state_attached_is_noop(self):
        server = make_server()
        await dispatch(server, LVACommand.START_LISTENING.value)  # should not raise


# ---------------------------------------------------------------------------
# _dispatch_command() — voice pipeline
# ---------------------------------------------------------------------------


class TestDispatchStartListening:
    @pytest.mark.asyncio
    async def test_calls_satellite_start_listening(self, tmp_path):
        state = make_state(tmp_path, muted=False)
        satellite = MagicMock()
        state.satellite = satellite
        server = make_server(state)

        await dispatch(server, LVACommand.START_LISTENING.value)

        satellite.start_listening.assert_called_once()

    @pytest.mark.asyncio
    async def test_noop_when_muted(self, tmp_path):
        state = make_state(tmp_path, muted=True)
        satellite = MagicMock()
        state.satellite = satellite
        server = make_server(state)

        await dispatch(server, LVACommand.START_LISTENING.value)

        satellite.start_listening.assert_not_called()

    @pytest.mark.asyncio
    async def test_noop_when_no_satellite(self, tmp_path):
        state = make_state(tmp_path, muted=False)
        state.satellite = None
        server = make_server(state)

        await dispatch(server, LVACommand.START_LISTENING.value)  # should not raise


class TestDispatchStopPipeline:
    @pytest.mark.asyncio
    async def test_calls_satellite_stop(self, tmp_path):
        state = make_state(tmp_path)
        satellite = MagicMock()
        state.satellite = satellite
        server = make_server(state)

        await dispatch(server, LVACommand.STOP_PIPELINE.value)

        satellite.stop.assert_called_once()

    @pytest.mark.asyncio
    async def test_noop_when_no_satellite(self, tmp_path):
        state = make_state(tmp_path)
        state.satellite = None
        server = make_server(state)

        await dispatch(server, LVACommand.STOP_PIPELINE.value)  # should not raise


# ---------------------------------------------------------------------------
# _dispatch_command() — microphone mute
# ---------------------------------------------------------------------------


class TestDispatchMute:
    @pytest.mark.asyncio
    async def test_mute_mic_calls_set_muted(self, tmp_path):
        state = make_state(tmp_path, muted=False)
        satellite = MagicMock()
        state.satellite = satellite
        server = make_server(state)

        await dispatch(server, LVACommand.MUTE_MIC.value)

        satellite._set_muted.assert_called_once_with(True)  # pylint: disable=protected-access

    @pytest.mark.asyncio
    async def test_mute_mic_noop_when_already_muted(self, tmp_path):
        state = make_state(tmp_path, muted=True)
        satellite = MagicMock()
        state.satellite = satellite
        server = make_server(state)

        await dispatch(server, LVACommand.MUTE_MIC.value)

        satellite._set_muted.assert_not_called()  # pylint: disable=protected-access

    @pytest.mark.asyncio
    async def test_unmute_mic_calls_set_muted(self, tmp_path):
        state = make_state(tmp_path, muted=True)
        satellite = MagicMock()
        state.satellite = satellite
        server = make_server(state)

        await dispatch(server, LVACommand.UNMUTE_MIC.value)

        satellite._set_muted.assert_called_once_with(False)  # pylint: disable=protected-access

    @pytest.mark.asyncio
    async def test_unmute_mic_noop_when_already_unmuted(self, tmp_path):
        state = make_state(tmp_path, muted=False)
        satellite = MagicMock()
        state.satellite = satellite
        server = make_server(state)

        await dispatch(server, LVACommand.UNMUTE_MIC.value)

        satellite._set_muted.assert_not_called()  # pylint: disable=protected-access

    @pytest.mark.asyncio
    async def test_mute_mic_pushes_switch_state_to_ha(self, tmp_path):
        state = make_state(tmp_path, muted=False)
        state.mute_switch_entity = MagicMock(key=1)
        satellite = MagicMock()
        state.satellite = satellite
        server = make_server(state)

        await dispatch(server, LVACommand.MUTE_MIC.value)

        satellite.send_messages.assert_called_once()


# ---------------------------------------------------------------------------
# _dispatch_command() — volume
# ---------------------------------------------------------------------------


class TestDispatchVolume:
    @pytest.mark.asyncio
    async def test_volume_up_increases_volume(self, tmp_path):
        state = make_state(tmp_path, volume=0.5)
        server = make_server(state)

        await dispatch(server, LVACommand.VOLUME_UP.value)

        assert state.volume == pytest.approx(0.5 + PeripheralAPIServer.DEFAULT_VOLUME_STEP)

    @pytest.mark.asyncio
    async def test_volume_down_decreases_volume(self, tmp_path):
        state = make_state(tmp_path, volume=0.5)
        server = make_server(state)

        await dispatch(server, LVACommand.VOLUME_DOWN.value)

        assert state.volume == pytest.approx(0.5 - PeripheralAPIServer.DEFAULT_VOLUME_STEP)

    @pytest.mark.asyncio
    async def test_volume_up_clamped_at_one(self, tmp_path):
        state = make_state(tmp_path, volume=1.0)
        server = make_server(state)

        await dispatch(server, LVACommand.VOLUME_UP.value)

        assert state.volume == pytest.approx(1.0)

    @pytest.mark.asyncio
    async def test_volume_down_clamped_at_zero(self, tmp_path):
        state = make_state(tmp_path, volume=0.0)
        server = make_server(state)

        await dispatch(server, LVACommand.VOLUME_DOWN.value)

        assert state.volume == pytest.approx(0.0)

    @pytest.mark.asyncio
    async def test_volume_up_updates_music_and_tts_players(self, tmp_path):
        state = make_state(tmp_path, volume=0.5)
        server = make_server(state)

        await dispatch(server, LVACommand.VOLUME_UP.value)

        state.music_player.set_volume.assert_called_once()
        state.tts_player.set_volume.assert_called_once()

    @pytest.mark.asyncio
    async def test_volume_up_pushes_media_player_state(self, tmp_path):
        state = make_state(tmp_path, volume=0.5)
        state.media_player_entity = make_media_entity()
        satellite = MagicMock()
        state.satellite = satellite
        server = make_server(state)

        await dispatch(server, LVACommand.VOLUME_UP.value)

        satellite.send_messages.assert_called_once()

    @pytest.mark.asyncio
    async def test_set_volume_sets_exact_value(self, tmp_path):
        state = make_state(tmp_path, volume=0.2)
        server = make_server(state)

        await dispatch(server, LVACommand.SET_VOLUME.value, {"volume": 0.9})

        assert state.volume == pytest.approx(0.9)

    @pytest.mark.asyncio
    async def test_set_volume_clamps_above_one(self, tmp_path):
        state = make_state(tmp_path, volume=0.2)
        server = make_server(state)

        await dispatch(server, LVACommand.SET_VOLUME.value, {"volume": 5.0})

        assert state.volume == pytest.approx(1.0)

    @pytest.mark.asyncio
    async def test_set_volume_clamps_below_zero(self, tmp_path):
        state = make_state(tmp_path, volume=0.2)
        server = make_server(state)

        await dispatch(server, LVACommand.SET_VOLUME.value, {"volume": -5.0})

        assert state.volume == pytest.approx(0.0)

    @pytest.mark.asyncio
    async def test_set_volume_ignores_non_numeric(self, tmp_path):
        state = make_state(tmp_path, volume=0.2)
        server = make_server(state)

        await dispatch(server, LVACommand.SET_VOLUME.value, {"volume": "loud"})

        assert state.volume == pytest.approx(0.2)

    @pytest.mark.asyncio
    async def test_set_volume_accepts_int(self, tmp_path):
        state = make_state(tmp_path, volume=0.2)
        server = make_server(state)

        await dispatch(server, LVACommand.SET_VOLUME.value, {"volume": 1})

        assert state.volume == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# _dispatch_command() — timer
# ---------------------------------------------------------------------------


class TestDispatchStopTimerRinging:
    @pytest.mark.asyncio
    async def test_stops_ringing_timer(self, tmp_path):
        state = make_state(tmp_path)
        satellite = MagicMock()
        satellite._timer_finished = True  # pylint: disable=protected-access
        state.satellite = satellite
        state.active_wake_words.add(state.stop_word.id)
        server = make_server(state)

        await dispatch(server, LVACommand.STOP_TIMER_RINGING.value)

        assert satellite._timer_finished is False  # pylint: disable=protected-access
        assert state.stop_word.id not in state.active_wake_words
        state.tts_player.stop.assert_called_once()
        satellite.unduck.assert_called_once()

    @pytest.mark.asyncio
    async def test_noop_when_not_ringing(self, tmp_path):
        state = make_state(tmp_path)
        satellite = MagicMock()
        satellite._timer_finished = False  # pylint: disable=protected-access
        state.satellite = satellite
        server = make_server(state)

        await dispatch(server, LVACommand.STOP_TIMER_RINGING.value)

        state.tts_player.stop.assert_not_called()

    @pytest.mark.asyncio
    async def test_noop_when_no_satellite(self, tmp_path):
        state = make_state(tmp_path)
        state.satellite = None
        server = make_server(state)

        await dispatch(server, LVACommand.STOP_TIMER_RINGING.value)  # should not raise


# ---------------------------------------------------------------------------
# _dispatch_command() — media player
# ---------------------------------------------------------------------------


class TestDispatchMediaPlayer:
    @pytest.mark.asyncio
    async def test_stop_media_player_stops_music(self, tmp_path):
        state = make_state(tmp_path)
        server = make_server(state)

        await dispatch(server, LVACommand.STOP_MEDIA_PLAYER.value)

        state.music_player.stop.assert_called_once()

    @pytest.mark.asyncio
    async def test_pause_media_player_pauses_music(self, tmp_path):
        state = make_state(tmp_path)
        server = make_server(state)

        await dispatch(server, LVACommand.PAUSE_MEDIA_PLAYER.value)

        state.music_player.pause.assert_called_once()

    @pytest.mark.asyncio
    async def test_resume_media_player_resumes_music(self, tmp_path):
        state = make_state(tmp_path)
        server = make_server(state)

        await dispatch(server, LVACommand.RESUME_MEDIA_PLAYER.value)

        state.music_player.resume.assert_called_once()

    @pytest.mark.asyncio
    async def test_stop_media_player_updates_entity_state(self, tmp_path):
        from aioesphomeapi.model import MediaPlayerState  # pylint: disable=import-outside-toplevel

        state = make_state(tmp_path)
        state.media_player_entity = make_media_entity()
        server = make_server(state)

        await dispatch(server, LVACommand.STOP_MEDIA_PLAYER.value)

        assert state.media_player_entity.state == MediaPlayerState.IDLE


# ---------------------------------------------------------------------------
# _dispatch_command() — button events
# ---------------------------------------------------------------------------


class TestDispatchButtonPress:
    @pytest.mark.asyncio
    async def test_single_press_updates_entity(self, tmp_path):
        state = make_state(tmp_path)
        state.button_event_sensor_entity = MagicMock()
        server = make_server(state)

        await dispatch(server, LVACommand.BUTTON_SINGLE_PRESS.value)

        state.button_event_sensor_entity.update_state.assert_called_once_with("single_press")

    @pytest.mark.asyncio
    async def test_single_press_does_not_play_sound(self, tmp_path):
        state = make_state(tmp_path)
        state.button_event_sensor_entity = MagicMock()
        server = make_server(state)

        await dispatch(server, LVACommand.BUTTON_SINGLE_PRESS.value)

        state.tts_player.play.assert_not_called()

    @pytest.mark.asyncio
    async def test_double_press_plays_sound_and_updates_entity(self, tmp_path):
        state = make_state(tmp_path)
        state.button_event_sensor_entity = MagicMock()
        server = make_server(state)

        await dispatch(server, LVACommand.BUTTON_DOUBLE_PRESS.value)

        state.tts_player.play.assert_called_once_with(state.button_double_press_sound)
        state.button_event_sensor_entity.update_state.assert_called_once_with("double_press")

    @pytest.mark.asyncio
    async def test_triple_press_plays_sound_and_updates_entity(self, tmp_path):
        state = make_state(tmp_path)
        state.button_event_sensor_entity = MagicMock()
        server = make_server(state)

        await dispatch(server, LVACommand.BUTTON_TRIPLE_PRESS.value)

        state.tts_player.play.assert_called_once_with(state.button_triple_press_sound)
        state.button_event_sensor_entity.update_state.assert_called_once_with("triple_press")

    @pytest.mark.asyncio
    async def test_long_press_plays_sound_and_updates_entity(self, tmp_path):
        state = make_state(tmp_path)
        state.button_event_sensor_entity = MagicMock()
        server = make_server(state)

        await dispatch(server, LVACommand.BUTTON_LONG_PRESS.value)

        state.tts_player.play.assert_called_once_with(state.button_long_press_sound)
        state.button_event_sensor_entity.update_state.assert_called_once_with("long_press")

    @pytest.mark.asyncio
    async def test_button_press_noop_when_entity_not_registered(self, tmp_path):
        state = make_state(tmp_path)
        state.button_event_sensor_entity = None
        server = make_server(state)

        await dispatch(server, LVACommand.BUTTON_SINGLE_PRESS.value)  # should not raise


# ---------------------------------------------------------------------------
# register_light() / register_button()
# ---------------------------------------------------------------------------


class TestRegisterLight:
    def test_missing_object_id_is_ignored(self, tmp_path):
        state = make_state(tmp_path)
        server = make_server(state)

        server._register_light({}, None)  # pylint: disable=protected-access

        assert state.pending_lights == []

    def test_adds_light_registration(self, tmp_path):
        state = make_state(tmp_path)
        server = make_server(state)

        server._register_light(  # pylint: disable=protected-access
            {"name": "LEDs", "object_id": "leds", "effects": ["Voice Assistant"]},
            None,
        )

        assert len(state.pending_lights) == 1
        assert state.pending_lights[0].object_id == "leds"

    def test_duplicate_object_id_is_ignored(self, tmp_path):
        state = make_state(tmp_path)
        server = make_server(state)
        data = {"name": "LEDs", "object_id": "leds"}

        server._register_light(data, None)  # pylint: disable=protected-access
        server._register_light(data, None)  # pylint: disable=protected-access

        assert len(state.pending_lights) == 1

    def test_calls_register_pending_lights_on_satellite(self, tmp_path):
        state = make_state(tmp_path)
        server = make_server(state)
        satellite = MagicMock()

        server._register_light({"object_id": "leds"}, satellite)  # pylint: disable=protected-access

        satellite.register_pending_lights.assert_called_once()

    def test_no_state_is_noop(self):
        server = make_server()
        server._register_light({"object_id": "leds"}, None)  # pylint: disable=protected-access  # should not raise


class TestRegisterButton:
    def test_sets_pending_button(self, tmp_path):
        state = make_state(tmp_path)
        server = make_server(state)

        server._register_button(None)  # pylint: disable=protected-access

        assert state.pending_button is True

    def test_idempotent_when_already_registered(self, tmp_path):
        state = make_state(tmp_path)
        state.pending_button = True
        server = make_server(state)
        satellite = MagicMock()

        server._register_button(satellite)  # pylint: disable=protected-access

        satellite.register_pending_button.assert_not_called()

    def test_calls_register_pending_button_on_satellite(self, tmp_path):
        state = make_state(tmp_path)
        server = make_server(state)
        satellite = MagicMock()

        server._register_button(satellite)  # pylint: disable=protected-access

        satellite.register_pending_button.assert_called_once()

    def test_no_state_is_noop(self):
        server = make_server()
        server._register_button(None)  # pylint: disable=protected-access  # should not raise


# ---------------------------------------------------------------------------
# _dispatch_command() — register_light / register_button routing
# ---------------------------------------------------------------------------


class TestDispatchRegistration:
    @pytest.mark.asyncio
    async def test_register_light_command_routes_correctly(self, tmp_path):
        state = make_state(tmp_path)
        server = make_server(state)

        await dispatch(server, LVACommand.REGISTER_LIGHT.value, {"object_id": "leds"})

        assert len(state.pending_lights) == 1

    @pytest.mark.asyncio
    async def test_register_button_command_routes_correctly(self, tmp_path):
        state = make_state(tmp_path)
        server = make_server(state)

        await dispatch(server, LVACommand.REGISTER_BUTTON.value)

        assert state.pending_button is True


# ---------------------------------------------------------------------------
# emit_event()
# ---------------------------------------------------------------------------


class TestEmitEvent:
    @pytest.mark.asyncio
    async def test_broadcasts_to_all_clients(self):
        server = make_server()
        ws1, ws2 = AsyncMock(), AsyncMock()
        server._clients = {ws1, ws2}  # pylint: disable=protected-access

        await server.emit_event(LVAEvent.IDLE)

        ws1.send.assert_awaited_once()
        ws2.send.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_no_clients_does_not_raise(self):
        server = make_server()
        await server.emit_event(LVAEvent.IDLE)  # should not raise

    @pytest.mark.asyncio
    async def test_payload_includes_data(self):
        server = make_server()
        ws = AsyncMock()
        server._clients = {ws}  # pylint: disable=protected-access

        await server.emit_event(LVAEvent.STT_TEXT, {"text": "hello"})

        sent = json.loads(ws.send.call_args.args[0])
        assert sent["data"]["text"] == "hello"

    @pytest.mark.asyncio
    async def test_dead_client_is_removed(self):
        server = make_server()
        dead_ws = AsyncMock()
        dead_ws.send.side_effect = ConnectionError
        alive_ws = AsyncMock()
        server._clients = {dead_ws, alive_ws}  # pylint: disable=protected-access

        await server.emit_event(LVAEvent.IDLE)

        assert dead_ws not in server._clients  # pylint: disable=protected-access
        assert alive_ws in server._clients  # pylint: disable=protected-access

    @pytest.mark.asyncio
    async def test_caches_last_stt_text(self):
        server = make_server()
        await server.emit_event(LVAEvent.STT_TEXT, {"text": "turn on the lights"})
        assert server._last_stt_text == "turn on the lights"  # pylint: disable=protected-access

    @pytest.mark.asyncio
    async def test_caches_last_tts_text(self):
        server = make_server()
        await server.emit_event(LVAEvent.TTS_TEXT, {"text": "OK"})
        assert server._last_tts_text == "OK"  # pylint: disable=protected-access

    @pytest.mark.asyncio
    async def test_listening_clears_cached_text(self):
        server = make_server()
        server._last_stt_text = "old"  # pylint: disable=protected-access
        server._last_tts_text = "old"  # pylint: disable=protected-access

        await server.emit_event(LVAEvent.LISTENING)

        assert server._last_stt_text is None  # pylint: disable=protected-access
        assert server._last_tts_text is None  # pylint: disable=protected-access

    @pytest.mark.asyncio
    async def test_tracks_current_state_for_state_events(self):
        server = make_server()
        await server.emit_event(LVAEvent.TTS_SPEAKING)
        assert server._current_state == LVAEvent.TTS_SPEAKING  # pylint: disable=protected-access

    @pytest.mark.asyncio
    async def test_does_not_track_transient_events(self):
        server = make_server()
        await server.emit_event(LVAEvent.STT_TEXT, {"text": "hi"})
        assert server._current_state is None  # pylint: disable=protected-access

    @pytest.mark.asyncio
    async def test_timer_updated_refreshes_ticking_data(self):
        server = make_server()
        await server.emit_event(LVAEvent.TIMER_TICKING, {"seconds_left": 30})
        await server.emit_event(LVAEvent.TIMER_UPDATED, {"seconds_left": 25})

        assert server._current_state == LVAEvent.TIMER_TICKING  # pylint: disable=protected-access
        assert server._current_state_data["seconds_left"] == 25  # pylint: disable=protected-access

    @pytest.mark.asyncio
    async def test_timer_updated_ignored_when_not_ticking(self):
        server = make_server()
        await server.emit_event(LVAEvent.IDLE)
        await server.emit_event(LVAEvent.TIMER_UPDATED, {"seconds_left": 25})

        assert server._current_state == LVAEvent.IDLE  # pylint: disable=protected-access


# ---------------------------------------------------------------------------
# emit_event_sync()
# ---------------------------------------------------------------------------


class TestEmitEventSync:
    def test_noop_when_no_loop(self):
        server = make_server()
        server.emit_event_sync(LVAEvent.IDLE)  # should not raise

    def test_noop_when_loop_closed(self):
        server = make_server()
        loop = MagicMock()
        loop.is_closed.return_value = True
        server._loop = loop  # pylint: disable=protected-access

        with patch("asyncio.run_coroutine_threadsafe") as mock_run:
            server.emit_event_sync(LVAEvent.IDLE)

        mock_run.assert_not_called()

    def test_schedules_coroutine_on_loop(self):
        server = make_server()
        loop = MagicMock()
        loop.is_closed.return_value = False
        server._loop = loop  # pylint: disable=protected-access

        with patch("asyncio.run_coroutine_threadsafe") as mock_run:
            server.emit_event_sync(LVAEvent.IDLE, {"foo": "bar"})

        mock_run.assert_called_once()
        assert mock_run.call_args.args[1] is loop


# ---------------------------------------------------------------------------
# _create_media_player_response()
# ---------------------------------------------------------------------------


class TestCreateMediaPlayerResponse:
    def test_returns_response_with_entity_fields(self, tmp_path):
        from aioesphomeapi.model import MediaPlayerState  # pylint: disable=import-outside-toplevel

        state = make_state(tmp_path)
        state.media_player_entity = make_media_entity(key=3, volume=0.4, muted=True)
        server = make_server(state)

        response = server._create_media_player_response(MediaPlayerState.PLAYING)  # pylint: disable=protected-access

        assert response.key == 3
        assert response.volume == pytest.approx(0.4)
        assert response.muted is True
        assert response.state == MediaPlayerState.PLAYING
