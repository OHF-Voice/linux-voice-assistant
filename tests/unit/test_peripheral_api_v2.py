"""Unit tests for version 2 of the peripheral WebSocket API (PeripheralApiV2)."""

import asyncio
import json
import logging
from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from linux_voice_assistant.peripheral_api.v1 import LVAEvent
from linux_voice_assistant.peripheral_api.v2 import _VOLUME_LIMIT_SOUND, _VOLUME_STEP_SOUND, PeripheralApiV2
from tests.unit.conftest import make_state


class FakeWebSocket:
    """A client that sends the given messages and records what it receives."""

    def __init__(self, incoming: Optional[List[str]] = None) -> None:
        self.send = AsyncMock()
        self.remote_address = ("127.0.0.1", 1234)
        self._incoming = incoming or []

    def __aiter__(self):
        return self._messages()

    async def _messages(self):
        for raw in self._incoming:
            yield raw

    def sent(self) -> List[Dict[str, Any]]:
        return [json.loads(call.args[0]) for call in self.send.await_args_list]


def make_server(tmp_path, **state_overrides) -> PeripheralApiV2:
    server = PeripheralApiV2()
    satellite = MagicMock()
    satellite.timer_ringing = False
    satellite.announcing = False
    state = make_state(tmp_path, volume=0.5, **state_overrides)
    state.satellite = satellite
    state.connected = True
    server.set_state(state)
    return server


async def dispatch(server: PeripheralApiV2, command: str, data: Optional[dict] = None) -> None:
    payload: Dict[str, Any] = {"command": command}
    if data is not None:
        payload["data"] = data
    await server._dispatch_command(json.dumps(payload))  # pylint: disable=protected-access


def connect(server: PeripheralApiV2) -> FakeWebSocket:
    ws = FakeWebSocket()
    server._clients.add(ws)  # pylint: disable=protected-access
    return ws


class TestHandleClient:
    @pytest.mark.asyncio
    async def test_hello_comes_first_then_the_whole_status(self, tmp_path):
        server = make_server(tmp_path)
        ws = FakeWebSocket()

        await server.handle_client(ws)

        sent = ws.sent()
        assert sent[0] == {"event": "hello", "data": {"api_version": "v2", "supported_versions": ["v1", "v2"]}}
        assert len(sent) == 9
        assert (sent[1]["event"], sent[-1]["event"]) == ("lva_status", "media_player_status")
        assert sent[1]["data"] == {"status": "connected"}
        assert {"event": "volume_status", "data": {"volume": 50}} in sent

    @pytest.mark.asyncio
    async def test_client_is_removed_when_it_disconnects(self, tmp_path):
        server = make_server(tmp_path)
        await server.handle_client(FakeWebSocket())
        assert not server._clients  # pylint: disable=protected-access

    @pytest.mark.asyncio
    async def test_commands_of_the_client_are_executed(self, tmp_path):
        server = make_server(tmp_path)
        ws = FakeWebSocket([json.dumps({"command": "start_listening"})])

        await server.handle_client(ws)

        server._state.satellite.start_listening.assert_called_once()  # pylint: disable=protected-access


class TestEmitEvent:
    @pytest.mark.asyncio
    async def test_wake_word_then_listening(self, tmp_path):
        server = make_server(tmp_path)
        await server.refresh_status()
        ws = connect(server)

        await server.emit_event(LVAEvent.WAKE_WORD_DETECTED, {"wake_word_id": "x"})
        await server.emit_event(LVAEvent.LISTENING)

        assert ws.sent() == [{"event": "state", "data": {"state": "wake_word"}}, {"event": "state", "data": {"state": "listening"}}]

    @pytest.mark.asyncio
    async def test_unchanged_status_is_not_sent_again(self, tmp_path):
        server = make_server(tmp_path)
        await server.refresh_status()
        ws = connect(server)

        await server.emit_event(LVAEvent.IDLE)
        await server.emit_event(LVAEvent.STT_TEXT, {"text": "hi"})

        ws.send.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_pipeline_error_is_sent_and_ends_in_idle(self, tmp_path):
        server = make_server(tmp_path)
        await server.emit_event(LVAEvent.THINKING)
        ws = connect(server)

        await server.emit_event(LVAEvent.PIPELINE_ERROR, {"reason": "stt-failed"})

        assert ws.sent() == [{"event": "pipeline_error", "data": {"reason": "stt-failed"}}, {"event": "state", "data": {"state": "idle"}}]

    @pytest.mark.asyncio
    async def test_pipeline_error_without_a_reason_says_unknown(self, tmp_path):
        server = make_server(tmp_path)
        ws = connect(server)

        await server.emit_event(LVAEvent.PIPELINE_ERROR)

        assert ws.sent()[0] == {"event": "pipeline_error", "data": {"reason": "unknown"}}

    @pytest.mark.asyncio
    async def test_mute_volume_and_connection_are_read_from_the_state(self, tmp_path):
        server = make_server(tmp_path)
        await server.refresh_status()
        ws = connect(server)
        server._state.muted = True  # pylint: disable=protected-access
        server._state.volume = 0.25  # pylint: disable=protected-access
        server._state.connected = False  # pylint: disable=protected-access

        await server.emit_event(LVAEvent.MUTED, {"muted": True})

        assert ws.sent() == [
            {"event": "lva_status", "data": {"status": "disconnected"}},
            {"event": "mute_status", "data": {"muted": True}},
            {"event": "volume_status", "data": {"volume": 25}},
        ]

    @pytest.mark.asyncio
    async def test_timer_lifecycle(self, tmp_path):
        server = make_server(tmp_path)
        await server.refresh_status()
        ws = connect(server)
        timer = {"id": "t1", "name": "", "total_seconds": 60, "seconds_left": 60}

        await server.emit_event(LVAEvent.TIMER_TICKING, timer)
        assert {"event": "timer_status", "data": {"status": "active"}} in ws.sent()
        assert {"event": "timer_remaining_time", "data": {"seconds": 60}} in ws.sent()
        assert {"event": "timer_remaining_percentage", "data": {"percentage": 100}} in ws.sent()

        ws.send.reset_mock()
        server._state.satellite.timer_ringing = True  # pylint: disable=protected-access
        await server.emit_event(LVAEvent.TIMER_RINGING, {**timer, "seconds_left": 0})
        assert ws.sent()[0] == {"event": "timer_status", "data": {"status": "ringing"}}

        ws.send.reset_mock()
        server._state.satellite.timer_ringing = False  # pylint: disable=protected-access
        await server.emit_event(LVAEvent.IDLE)
        assert ws.sent() == [{"event": "timer_status", "data": {"status": "idle"}}]
        await server.stop()

    @pytest.mark.asyncio
    async def test_auto_stopped_timer_is_reported_before_the_status(self, tmp_path):
        server = make_server(tmp_path)
        server._state.satellite.timer_ringing = True  # pylint: disable=protected-access
        await server.refresh_status()
        ws = connect(server)
        server._state.satellite.timer_ringing = False  # pylint: disable=protected-access

        await server.emit_event(LVAEvent.TIMER_AUTO_STOPPED, {"seconds": 900})

        assert ws.sent() == [{"event": "timer_auto_stopped", "data": {"seconds": 900}}, {"event": "timer_status", "data": {"status": "idle"}}]

    @pytest.mark.asyncio
    async def test_cancelled_timer_goes_back_to_idle(self, tmp_path):
        server = make_server(tmp_path)
        timer = {"id": "t1", "name": "", "total_seconds": 60, "seconds_left": 60}
        await server.emit_event(LVAEvent.TIMER_TICKING, timer)
        ws = connect(server)

        await server.emit_event(LVAEvent.TIMER_UPDATED, {**timer, "seconds_left": 0})

        assert {"event": "timer_status", "data": {"status": "idle"}} in ws.sent()
        assert {"event": "timer_remaining_time", "data": {"seconds": 0}} in ws.sent()
        await server.stop()

    @pytest.mark.asyncio
    async def test_dead_client_is_dropped(self, tmp_path):
        server = make_server(tmp_path)
        dead = connect(server)
        dead.send.side_effect = RuntimeError("closed")

        await server.emit_event(LVAEvent.LISTENING)

        assert dead not in server._clients  # pylint: disable=protected-access

    @pytest.mark.asyncio
    async def test_status_change_is_logged_at_debug_level(self, tmp_path, caplog):
        server = make_server(tmp_path)
        await server.refresh_status()

        with caplog.at_level(logging.DEBUG):
            await server.emit_event(LVAEvent.LISTENING)

        assert "Peripheral API v2 status: state=listening" in caplog.text


class TestStatusFromState:
    @pytest.mark.asyncio
    async def test_media_player_state_and_silent(self, tmp_path):
        server = make_server(tmp_path)
        entity = MagicMock()
        entity.announcing = False
        entity.state.name = "PLAYING"
        server._state.media_player_entity = entity  # pylint: disable=protected-access
        ws = connect(server)

        await server.refresh_status()
        assert {"event": "media_player_status", "data": {"status": "playing"}} in ws.sent()

        ws.send.reset_mock()
        server._state.volume = 0.0  # pylint: disable=protected-access
        await server.refresh_status()
        assert {"event": "media_player_status", "data": {"status": "silent"}} in ws.sent()

    @pytest.mark.asyncio
    async def test_paused_media_player(self, tmp_path):
        server = make_server(tmp_path)
        entity = MagicMock()
        entity.announcing = False
        entity.state.name = "PAUSED"
        server._state.media_player_entity = entity  # pylint: disable=protected-access
        ws = connect(server)

        await server.refresh_status()

        assert {"event": "media_player_status", "data": {"status": "pause"}} in ws.sent()

    @pytest.mark.asyncio
    @pytest.mark.parametrize("source", ["satellite", "entity"])
    async def test_announcement_shows_announce(self, tmp_path, source):
        server = make_server(tmp_path)
        entity = MagicMock()
        entity.announcing = source == "entity"
        entity.state.name = "PLAYING"
        server._state.media_player_entity = entity  # pylint: disable=protected-access
        server._state.satellite.announcing = source == "satellite"  # pylint: disable=protected-access
        ws = connect(server)

        await server.refresh_status()

        assert {"event": "state", "data": {"state": "announce"}} in ws.sent()

    @pytest.mark.asyncio
    async def test_refresh_without_state_does_nothing(self):
        await PeripheralApiV2().refresh_status()


class TestTicker:
    @pytest.mark.asyncio
    async def test_time_left_is_counted_down(self, tmp_path):
        server = make_server(tmp_path)
        server.set_loop(asyncio.get_running_loop())
        timer = {"id": "t1", "name": "", "total_seconds": 60, "seconds_left": 60}
        await server.emit_event(LVAEvent.TIMER_TICKING, timer)
        ws = connect(server)

        with patch("linux_voice_assistant.peripheral_api.v2.time.monotonic", return_value=1000.0):
            with patch("linux_voice_assistant.peripheral_api.v2.asyncio.sleep", AsyncMock()):
                await server.refresh_status()
                await asyncio.sleep(0)
                await asyncio.sleep(0)

        assert any(m["event"] == "timer_remaining_time" for m in ws.sent())
        await server.stop()

    @pytest.mark.asyncio
    async def test_stop_cancels_the_ticker(self, tmp_path):
        server = make_server(tmp_path)
        server.set_loop(asyncio.get_running_loop())
        await server.emit_event(LVAEvent.TIMER_TICKING, {"id": "t1", "name": "", "total_seconds": 60, "seconds_left": 60})
        ticker = server._ticker  # pylint: disable=protected-access
        assert ticker is not None

        await server.stop()
        await asyncio.sleep(0)

        assert ticker.cancelled()
        assert server._ticker is None  # pylint: disable=protected-access

    @pytest.mark.asyncio
    async def test_no_ticker_without_timers(self, tmp_path):
        server = make_server(tmp_path)
        server.set_loop(asyncio.get_running_loop())
        await server.refresh_status()
        assert server._ticker is None  # pylint: disable=protected-access


class TestCommandParsing:
    @pytest.mark.asyncio
    async def test_invalid_json_warns(self, tmp_path, caplog):
        server = make_server(tmp_path)
        with caplog.at_level(logging.WARNING):
            await server._dispatch_command("not json")  # pylint: disable=protected-access
        assert "invalid JSON" in caplog.text

    @pytest.mark.asyncio
    async def test_unknown_command_warns(self, tmp_path, caplog):
        server = make_server(tmp_path)
        with caplog.at_level(logging.WARNING):
            await dispatch(server, "dance")
        assert "unknown command 'dance'" in caplog.text

    @pytest.mark.asyncio
    async def test_v1_command_names_are_unknown(self, tmp_path, caplog):
        server = make_server(tmp_path)
        with caplog.at_level(logging.WARNING):
            await dispatch(server, "mute_mic")
        assert "unknown command 'mute_mic'" in caplog.text

    @pytest.mark.asyncio
    async def test_message_without_command_is_ignored(self, tmp_path):
        server = make_server(tmp_path)
        await server._dispatch_command(json.dumps({"foo": 1}))  # pylint: disable=protected-access
        await server._dispatch_command(json.dumps([1]))  # pylint: disable=protected-access

    @pytest.mark.asyncio
    async def test_command_without_state_is_ignored(self):
        await dispatch(PeripheralApiV2(), "start_listening")


class TestStartListeningAndStop:
    @pytest.mark.asyncio
    async def test_a_timer_ended_by_a_wake_word_is_reported_before_the_wake_word(self, tmp_path):
        server = make_server(tmp_path)
        server._state.satellite.timer_ringing = True  # pylint: disable=protected-access
        await server.refresh_status()
        ws = connect(server)
        server._state.satellite.timer_ringing = False  # pylint: disable=protected-access

        await server.emit_event(LVAEvent.WAKE_WORD_DETECTED)

        assert [m["event"] for m in ws.sent()] == ["timer_status", "state"]

    @pytest.mark.asyncio
    async def test_start_listening_that_ends_a_ringing_timer_updates_the_status_at_once(self, tmp_path):
        server = make_server(tmp_path)
        server._state.satellite.timer_ringing = True  # pylint: disable=protected-access
        await server.refresh_status()
        ws = connect(server)

        def stop_ringing():
            server._state.satellite.timer_ringing = False  # pylint: disable=protected-access

        server._state.satellite.start_listening.side_effect = stop_ringing  # pylint: disable=protected-access
        await dispatch(server, "start_listening")

        assert {"event": "timer_status", "data": {"status": "idle"}} in ws.sent()

    @pytest.mark.asyncio
    async def test_start_listening(self, tmp_path):
        server = make_server(tmp_path)
        await dispatch(server, "start_listening")
        server._state.satellite.start_listening.assert_called_once()  # pylint: disable=protected-access

    @pytest.mark.asyncio
    async def test_start_listening_is_ignored_while_muted(self, tmp_path):
        server = make_server(tmp_path)
        server._state.muted = True  # pylint: disable=protected-access
        await dispatch(server, "start_listening")
        server._state.satellite.start_listening.assert_not_called()  # pylint: disable=protected-access

    @pytest.mark.asyncio
    async def test_start_listening_without_satellite(self, tmp_path):
        server = make_server(tmp_path)
        server._state.satellite = None  # pylint: disable=protected-access
        await dispatch(server, "start_listening")

    @pytest.mark.asyncio
    async def test_stop_stops_the_pipeline(self, tmp_path):
        server = make_server(tmp_path)
        await dispatch(server, "stop")
        server._state.satellite.stop.assert_called_once()  # pylint: disable=protected-access

    @pytest.mark.asyncio
    async def test_stop_leaves_a_ringing_timer_alone(self, tmp_path):
        server = make_server(tmp_path)
        server._state.satellite.timer_ringing = True  # pylint: disable=protected-access
        await dispatch(server, "stop")
        server._state.satellite.stop.assert_not_called()  # pylint: disable=protected-access

    @pytest.mark.asyncio
    async def test_timer_stop_stops_a_ringing_timer(self, tmp_path):
        server = make_server(tmp_path)
        server._state.satellite.timer_ringing = True  # pylint: disable=protected-access
        await dispatch(server, "timer_stop")
        server._state.satellite.stop.assert_called_once()  # pylint: disable=protected-access

    @pytest.mark.asyncio
    async def test_timer_stop_is_ignored_without_a_ringing_timer(self, tmp_path):
        server = make_server(tmp_path)
        await dispatch(server, "timer_stop")
        server._state.satellite.stop.assert_not_called()  # pylint: disable=protected-access


class TestMute:
    @pytest.mark.asyncio
    async def test_mute_on(self, tmp_path):
        server = make_server(tmp_path)
        entity = MagicMock()
        entity.key = 7
        server._state.mute_switch_entity = entity  # pylint: disable=protected-access

        await dispatch(server, "mute_on")

        server._state.satellite._set_muted.assert_called_once_with(True)  # pylint: disable=protected-access
        assert entity._switch_state is True  # pylint: disable=protected-access
        server._state.satellite.send_messages.assert_called_once_with([entity.state_message.return_value])  # pylint: disable=protected-access

    @pytest.mark.asyncio
    @pytest.mark.parametrize("remote_unmute_enabled,message_type", [(True, "SwitchStateResponse"), (False, "BinarySensorStateResponse")])
    async def test_home_assistant_gets_the_message_of_the_real_mute_entity(self, tmp_path, remote_unmute_enabled, message_type):
        from linux_voice_assistant.entity import MuteSwitchEntity

        server = make_server(tmp_path)
        entity = MuteSwitchEntity(
            server=MagicMock(),
            key=7,
            name="Mute",
            object_id="mute",
            get_muted=lambda: False,
            set_muted=MagicMock(),
            remote_unmute_enabled=remote_unmute_enabled,
        )
        server._state.mute_switch_entity = entity  # pylint: disable=protected-access

        await dispatch(server, "mute_on")

        (message,) = server._state.satellite.send_messages.call_args.args[0]  # pylint: disable=protected-access
        assert (type(message).__name__, message.key, message.state) == (message_type, 7, True)

    @pytest.mark.asyncio
    async def test_a_peripheral_can_unmute_while_remote_unmute_is_disabled(self, tmp_path):
        server = make_server(tmp_path, remote_unmute_enabled=False)
        server._state.muted = True  # pylint: disable=protected-access

        await dispatch(server, "mute_off")

        server._state.satellite._set_muted.assert_called_once_with(False)  # pylint: disable=protected-access

    @pytest.mark.asyncio
    async def test_a_peripheral_can_mute_while_remote_unmute_is_disabled(self, tmp_path):
        server = make_server(tmp_path, remote_unmute_enabled=False)
        await dispatch(server, "mute_on")
        server._state.satellite._set_muted.assert_called_once_with(True)  # pylint: disable=protected-access

    @pytest.mark.asyncio
    async def test_mute_off(self, tmp_path):
        server = make_server(tmp_path)
        server._state.muted = True  # pylint: disable=protected-access

        await dispatch(server, "mute_off")

        server._state.satellite._set_muted.assert_called_once_with(False)  # pylint: disable=protected-access

    @pytest.mark.asyncio
    @pytest.mark.parametrize("command,muted", [("mute_on", True), ("mute_off", False)])
    async def test_already_in_that_state_is_ignored(self, tmp_path, command, muted):
        server = make_server(tmp_path)
        server._state.muted = muted  # pylint: disable=protected-access
        await dispatch(server, command)
        server._state.satellite._set_muted.assert_not_called()  # pylint: disable=protected-access

    @pytest.mark.asyncio
    async def test_mute_without_satellite(self, tmp_path):
        server = make_server(tmp_path)
        server._state.satellite = None  # pylint: disable=protected-access
        await dispatch(server, "mute_on")


class TestVolume:
    @pytest.mark.asyncio
    async def test_volume_up_by_one_step(self, tmp_path):
        server = make_server(tmp_path)
        await dispatch(server, "volume_up")
        server._state.music_player.set_volume.assert_called_once_with(55)  # pylint: disable=protected-access

    @pytest.mark.asyncio
    async def test_volume_down_by_one_step(self, tmp_path):
        server = make_server(tmp_path)
        await dispatch(server, "volume_down")
        server._state.music_player.set_volume.assert_called_once_with(45)  # pylint: disable=protected-access

    @pytest.mark.asyncio
    async def test_custom_step(self, tmp_path):
        server = make_server(tmp_path)
        server._volume_step = 0.1  # pylint: disable=protected-access
        await dispatch(server, "volume_up")
        server._state.music_player.set_volume.assert_called_once_with(60)  # pylint: disable=protected-access

    @pytest.mark.asyncio
    async def test_volume_up_at_the_limit_plays_the_feedback_sound_and_changes_nothing(self, tmp_path, caplog):
        server = make_server(tmp_path)
        server._state.volume = 1.0  # pylint: disable=protected-access

        with caplog.at_level(logging.DEBUG):
            await dispatch(server, "volume_up")

        server._state.tts_player.play.assert_called_once_with(str(_VOLUME_LIMIT_SOUND))  # pylint: disable=protected-access
        server._state.music_player.set_volume.assert_not_called()  # pylint: disable=protected-access
        assert "Volume is at the limit (100 %)" in caplog.text

    @pytest.mark.asyncio
    async def test_volume_down_at_zero_plays_the_limit_sound(self, tmp_path):
        server = make_server(tmp_path)
        server._state.volume = 0.0  # pylint: disable=protected-access

        await dispatch(server, "volume_down")

        server._state.tts_player.play.assert_called_once_with(str(_VOLUME_LIMIT_SOUND))  # pylint: disable=protected-access
        server._state.music_player.set_volume.assert_not_called()  # pylint: disable=protected-access

    def test_sounds_exist(self):
        assert _VOLUME_LIMIT_SOUND.is_file()
        assert _VOLUME_STEP_SOUND.is_file()

    @pytest.mark.asyncio
    @pytest.mark.parametrize("command", ["volume_up", "volume_down"])
    async def test_a_change_plays_the_step_sound_after_it_was_applied(self, tmp_path, caplog, command):
        server = make_server(tmp_path)
        calls = []
        server._state.music_player.set_volume.side_effect = lambda *_: calls.append("volume")  # pylint: disable=protected-access
        server._state.tts_player.play.side_effect = lambda *_: calls.append("sound")  # pylint: disable=protected-access

        with caplog.at_level(logging.DEBUG):
            await dispatch(server, command)

        assert calls == ["volume", "sound"]
        server._state.tts_player.play.assert_called_once_with(str(_VOLUME_STEP_SOUND))  # pylint: disable=protected-access
        assert "playing the step sound" in caplog.text

    @pytest.mark.asyncio
    async def test_volume_set_plays_no_sound(self, tmp_path):
        server = make_server(tmp_path)
        await dispatch(server, "volume_set", {"volume": 70})
        server._state.tts_player.play.assert_not_called()  # pylint: disable=protected-access

    @pytest.mark.asyncio
    @pytest.mark.parametrize("percent,expected", [(60, 60), (0, 0), (100, 100), (250, 100), (-5, 0)])
    async def test_volume_set_is_a_percentage_and_clamped(self, tmp_path, percent, expected):
        server = make_server(tmp_path)
        await dispatch(server, "volume_set", {"volume": percent})
        server._state.music_player.set_volume.assert_called_once_with(expected)  # pylint: disable=protected-access

    @pytest.mark.asyncio
    @pytest.mark.parametrize("data", [None, {}, {"volume": "loud"}, {"volume": True}, {"volume": None}])
    async def test_volume_set_with_invalid_data_warns(self, tmp_path, caplog, data):
        server = make_server(tmp_path)
        with caplog.at_level(logging.WARNING):
            await dispatch(server, "volume_set", data)
        server._state.music_player.set_volume.assert_not_called()  # pylint: disable=protected-access
        assert "invalid volume in volume_set" in caplog.text

    @pytest.mark.asyncio
    async def test_volume_goes_through_the_media_player_entity(self, tmp_path):
        server = make_server(tmp_path)
        entity = MagicMock()
        server._state.media_player_entity = entity  # pylint: disable=protected-access

        await dispatch(server, "volume_set", {"volume": 80})

        entity.set_volume.assert_called_once_with(0.8)
        server._state.music_player.set_volume.assert_not_called()  # pylint: disable=protected-access
        server._state.satellite.send_messages.assert_called_once_with([entity._get_state_message.return_value])  # pylint: disable=protected-access


class TestButtonPressed:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("press,sound", [("double", "/sounds/double_press.flac"), ("triple", "/sounds/triple_press.flac"), ("long", "/sounds/long_press.flac")])
    async def test_press_plays_its_sound(self, tmp_path, press, sound):
        server = make_server(tmp_path)
        await dispatch(server, "button_pressed", {"press": press})
        server._state.tts_player.play.assert_called_once_with(sound)  # pylint: disable=protected-access

    @pytest.mark.asyncio
    async def test_single_press_plays_nothing(self, tmp_path):
        server = make_server(tmp_path)
        await dispatch(server, "button_pressed", {"press": "single"})
        server._state.tts_player.play.assert_not_called()  # pylint: disable=protected-access

    @pytest.mark.asyncio
    @pytest.mark.parametrize("data", [None, {}, {"press": "huge"}, {"press": 1}])
    async def test_invalid_press_warns(self, tmp_path, caplog, data):
        server = make_server(tmp_path)
        with caplog.at_level(logging.WARNING):
            await dispatch(server, "button_pressed", data)
        server._state.tts_player.play.assert_not_called()  # pylint: disable=protected-access
        assert "invalid press in button_pressed" in caplog.text

    @pytest.mark.asyncio
    async def test_no_home_assistant_entity_is_touched(self, tmp_path):
        server = make_server(tmp_path)
        server._state.button_event_sensor_entity = MagicMock()  # pylint: disable=protected-access
        await dispatch(server, "button_pressed", {"press": "double"})
        server._state.button_event_sensor_entity.update_state.assert_not_called()  # pylint: disable=protected-access


class TestHardwareMuteStatus:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("status,muted", [("muted", True), ("unmuted", False)])
    async def test_status_is_kept(self, tmp_path, caplog, status, muted):
        server = make_server(tmp_path)
        with caplog.at_level(logging.DEBUG):
            await dispatch(server, "hardware_mute_status", {"status": status})
        assert server._state.hardware_muted is muted  # pylint: disable=protected-access
        assert f"Hardware mute reported: {status}" in caplog.text

    @pytest.mark.asyncio
    async def test_it_does_not_change_the_mute_of_lva(self, tmp_path):
        server = make_server(tmp_path)
        await dispatch(server, "hardware_mute_status", {"status": "muted"})
        server._state.satellite._set_muted.assert_not_called()  # pylint: disable=protected-access
        assert server._state.muted is False  # pylint: disable=protected-access

    @pytest.mark.asyncio
    async def test_nothing_is_passed_on_to_clients_or_home_assistant(self, tmp_path):
        server = make_server(tmp_path)
        other = connect(server)
        server._state.broadcast = MagicMock()  # pylint: disable=protected-access

        await dispatch(server, "hardware_mute_status", {"status": "muted"})
        await dispatch(server, "hardware_mute_type", {"type": "changeable"})

        other.send.assert_not_awaited()
        server._state.broadcast.assert_not_called()  # pylint: disable=protected-access
        server._state.satellite.send_messages.assert_not_called()  # pylint: disable=protected-access

    @pytest.mark.asyncio
    async def test_no_entity_is_added(self, tmp_path):
        server = make_server(tmp_path)
        before = list(server._state.entities)  # pylint: disable=protected-access
        await dispatch(server, "hardware_mute_status", {"status": "muted"})
        await dispatch(server, "hardware_mute_type", {"type": "read_only"})
        assert server._state.entities == before  # pylint: disable=protected-access

    @pytest.mark.asyncio
    async def test_an_unchanged_status_is_noted_in_the_log(self, tmp_path, caplog):
        server = make_server(tmp_path)
        await dispatch(server, "hardware_mute_status", {"status": "muted"})
        with caplog.at_level(logging.DEBUG):
            await dispatch(server, "hardware_mute_status", {"status": "muted"})
        assert "already reported as muted" in caplog.text

    @pytest.mark.asyncio
    @pytest.mark.parametrize("data", [None, {}, {"status": "on"}, {"status": True}, {"muted": True}])
    async def test_invalid_status_warns_and_changes_nothing(self, tmp_path, caplog, data):
        server = make_server(tmp_path)
        with caplog.at_level(logging.WARNING):
            await dispatch(server, "hardware_mute_status", data)
        assert server._state.hardware_muted is None  # pylint: disable=protected-access
        assert "invalid status in hardware_mute_status" in caplog.text


class TestHardwareMuteType:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("mute_type", ["none", "changeable", "read_only"])
    async def test_type_is_kept(self, tmp_path, caplog, mute_type):
        server = make_server(tmp_path)
        with caplog.at_level(logging.DEBUG):
            await dispatch(server, "hardware_mute_type", {"type": mute_type})
        assert server._state.hardware_mute_type == mute_type  # pylint: disable=protected-access
        assert f"Hardware mute type reported: {mute_type}" in caplog.text

    @pytest.mark.asyncio
    async def test_an_unchanged_type_is_noted_in_the_log(self, tmp_path, caplog):
        server = make_server(tmp_path)
        await dispatch(server, "hardware_mute_type", {"type": "none"})
        with caplog.at_level(logging.DEBUG):
            await dispatch(server, "hardware_mute_type", {"type": "none"})
        assert "already reported as none" in caplog.text

    @pytest.mark.asyncio
    @pytest.mark.parametrize("data", [None, {}, {"type": "maybe"}, {"type": True}, {"possible": True}, {"type": "Changeable"}])
    async def test_invalid_type_warns_and_changes_nothing(self, tmp_path, caplog, data):
        server = make_server(tmp_path)
        with caplog.at_level(logging.WARNING):
            await dispatch(server, "hardware_mute_type", data)
        assert server._state.hardware_mute_type is None  # pylint: disable=protected-access
        assert "invalid type in hardware_mute_type" in caplog.text

    @pytest.mark.asyncio
    async def test_the_old_name_is_an_unknown_command(self, tmp_path, caplog):
        server = make_server(tmp_path)
        with caplog.at_level(logging.WARNING):
            await dispatch(server, "hardware_mute_possible", {"possible": True})
        assert "unknown command 'hardware_mute_possible'" in caplog.text


class TestHardwareMuteInTheSnapshot:
    @pytest.mark.asyncio
    async def test_a_new_client_is_not_told_the_hardware_mute(self, tmp_path):
        server = make_server(tmp_path)
        server._state.hardware_muted = True  # pylint: disable=protected-access
        server._state.hardware_mute_type = "changeable"  # pylint: disable=protected-access
        ws = FakeWebSocket()
        await server.handle_client(ws)
        assert not [m for m in ws.sent() if m["event"].startswith("hardware_mute")]


class TestRequestHardwareMute:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("muted,event", [(True, "hardware_mute_on"), (False, "hardware_mute_off")])
    async def test_request_goes_to_every_peripheral_if_changeable(self, tmp_path, muted, event):
        server = make_server(tmp_path)
        server._state.hardware_mute_type = "changeable"  # pylint: disable=protected-access
        first, second = connect(server), connect(server)

        await server.request_hardware_mute(muted)

        assert first.sent() == second.sent() == [{"event": event}]

    @pytest.mark.asyncio
    @pytest.mark.parametrize("mute_type", ["none", "read_only", None])
    async def test_ignored_if_the_hardware_mute_cannot_be_changed(self, tmp_path, caplog, mute_type):
        server = make_server(tmp_path)
        server._state.hardware_mute_type = mute_type  # pylint: disable=protected-access
        ws = connect(server)

        with caplog.at_level(logging.DEBUG):
            await server.request_hardware_mute(True)

        ws.send.assert_not_awaited()
        assert f"Ignoring the request to set the hardware mute: type={mute_type}" in caplog.text

    @pytest.mark.asyncio
    async def test_ignored_without_a_connected_peripheral(self, tmp_path, caplog):
        server = make_server(tmp_path)
        server._state.hardware_mute_type = "changeable"  # pylint: disable=protected-access
        with caplog.at_level(logging.DEBUG):
            await server.request_hardware_mute(False)
        assert "0 peripheral(s) connected" in caplog.text

    @pytest.mark.asyncio
    async def test_a_sent_request_does_not_change_the_kept_state(self, tmp_path):
        server = make_server(tmp_path)
        server._state.hardware_mute_type = "changeable"  # pylint: disable=protected-access
        connect(server)
        await server.request_hardware_mute(True)
        assert server._state.hardware_muted is None  # pylint: disable=protected-access

    @pytest.mark.asyncio
    async def test_without_state_it_does_nothing(self):
        await PeripheralApiV2().request_hardware_mute(True)
