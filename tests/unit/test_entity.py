"""Unit tests for ESPHome entity classes."""

import pytest
from unittest.mock import MagicMock, call

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_server():
    """Minimal mock APIServer."""
    server = MagicMock()
    server.state = MagicMock()
    return server


def make_media_player(server=None, key=1, initial_volume=1.0):
    from linux_voice_assistant.entity import MediaPlayerEntity
    server = server or make_server()
    return MediaPlayerEntity(
        server=server,
        key=key,
        name="Media Player",
        object_id="test_media_player",
        music_player=MagicMock(),
        announce_player=MagicMock(),
        initial_volume=initial_volume,
    )


def make_mute_switch(server=None, key=2, initial_muted=False):
    from linux_voice_assistant.entity import MuteSwitchEntity
    server = server or make_server()
    get_muted = MagicMock(return_value=initial_muted)
    set_muted = MagicMock()
    entity = MuteSwitchEntity(
        server=server,
        key=key,
        name="Mute",
        object_id="mute",
        get_muted=get_muted,
        set_muted=set_muted,
    )
    entity._get_muted = get_muted
    entity._set_muted = set_muted
    return entity


def make_mic_setting(server=None, key=3, options=None, value=0.0):
    from linux_voice_assistant.entity import MicSettingEntity
    server = server or make_server()
    get_value = MagicMock(return_value=value)
    set_value = MagicMock()
    entity = MicSettingEntity(
        server=server,
        key=key,
        name="Mic Gain",
        object_id="mic_gain",
        get_value=get_value,
        set_value=set_value,
        min_value=0.0,
        max_value=31.0,
        options=options,
    )
    entity._get_value_mock = get_value
    entity._set_value_mock = set_value
    return entity


# ---------------------------------------------------------------------------
# Import proto message types once
# ---------------------------------------------------------------------------

from aioesphomeapi.api_pb2 import (
    ListEntitiesRequest,
    ListEntitiesMediaPlayerResponse,
    ListEntitiesSelectResponse,
    ListEntitiesNumberResponse,
    ListEntitiesSwitchResponse,
    MediaPlayerCommandRequest,
    MediaPlayerStateResponse,
    NumberCommandRequest,
    NumberStateResponse,
    SelectCommandRequest,
    SelectStateResponse,
    SubscribeHomeAssistantStatesRequest,
    SwitchCommandRequest,
    SwitchStateResponse,
)
from aioesphomeapi.model import MediaPlayerCommand, MediaPlayerState


# ---------------------------------------------------------------------------
# MediaPlayerEntity
# ---------------------------------------------------------------------------


class TestMediaPlayerEntityInit:
    def test_initial_volume_clamped_above_one(self):
        entity = make_media_player(initial_volume=1.5)
        assert entity.volume == 1.0

    def test_initial_volume_clamped_below_zero(self):
        entity = make_media_player(initial_volume=-0.5)
        assert entity.volume == 0.0

    def test_initial_volume_stored(self):
        entity = make_media_player(initial_volume=0.7)
        assert abs(entity.volume - 0.7) < 0.001

    def test_initial_state_is_idle(self):
        entity = make_media_player()
        assert entity.state == MediaPlayerState.IDLE

    def test_not_muted_by_default(self):
        entity = make_media_player()
        assert entity.muted is False


class TestMediaPlayerEntityListEntities:
    def test_list_entities_request_yields_response(self):
        entity = make_media_player(key=5)
        msgs = list(entity.handle_message(ListEntitiesRequest()))
        assert len(msgs) == 1
        assert isinstance(msgs[0], ListEntitiesMediaPlayerResponse)

    def test_list_entities_response_has_correct_key(self):
        entity = make_media_player(key=5)
        msgs = list(entity.handle_message(ListEntitiesRequest()))
        assert msgs[0].key == 5

    def test_list_entities_response_has_correct_object_id(self):
        entity = make_media_player()
        msgs = list(entity.handle_message(ListEntitiesRequest()))
        assert msgs[0].object_id == "test_media_player"

    def test_list_entities_supports_pause(self):
        entity = make_media_player()
        msgs = list(entity.handle_message(ListEntitiesRequest()))
        assert msgs[0].supports_pause is True


class TestMediaPlayerEntitySubscribeStates:
    def test_subscribe_states_yields_state_response(self):
        entity = make_media_player()
        msgs = list(entity.handle_message(SubscribeHomeAssistantStatesRequest()))
        assert len(msgs) == 1
        assert isinstance(msgs[0], MediaPlayerStateResponse)

    def test_subscribe_states_has_correct_key(self):
        entity = make_media_player(key=7)
        msgs = list(entity.handle_message(SubscribeHomeAssistantStatesRequest()))
        assert msgs[0].key == 7


class TestMediaPlayerEntityVolume:
    def test_apply_volume_sets_both_players(self):
        entity = make_media_player(initial_volume=1.0)
        entity._apply_volume(0.5, persist=False)
        entity.music_player.set_volume.assert_called_with(50)
        entity.announce_player.set_volume.assert_called_with(50)

    def test_apply_volume_clamps_above_one(self):
        entity = make_media_player()
        entity._apply_volume(1.5, persist=False)
        assert entity.volume == 1.0

    def test_apply_volume_clamps_below_zero(self):
        entity = make_media_player()
        entity._apply_volume(-0.1, persist=False)
        assert entity.volume == 0.0

    def test_apply_volume_stores_previous_volume(self):
        entity = make_media_player(initial_volume=0.8)
        entity._apply_volume(0.4, persist=False, remember=True)
        assert abs(entity.previous_volume - 0.4) < 0.001

    def test_apply_volume_persist_calls_callback(self):
        callback = MagicMock()
        entity = make_media_player()
        entity._on_volume_changed = callback
        entity._apply_volume(0.5, persist=True)
        callback.assert_called_once_with(0.5)

    def test_apply_volume_no_persist_skips_callback(self):
        callback = MagicMock()
        entity = make_media_player()
        entity._on_volume_changed = callback
        entity._apply_volume(0.5, persist=False)
        callback.assert_not_called()

    def test_volume_command_yields_state_response(self):
        entity = make_media_player(key=1)
        msg = MediaPlayerCommandRequest(key=1, has_volume=True, volume=0.6)
        msgs = list(entity.handle_message(msg))
        assert any(isinstance(m, MediaPlayerStateResponse) for m in msgs)

    def test_volume_command_wrong_key_ignored(self):
        entity = make_media_player(key=1)
        msg = MediaPlayerCommandRequest(key=99, has_volume=True, volume=0.6)
        msgs = list(entity.handle_message(msg))
        assert msgs == [] or not any(isinstance(m, MediaPlayerStateResponse) for m in msgs)


class TestMediaPlayerEntityMuteUnmute:
    def test_mute_sets_volume_to_zero(self):
        entity = make_media_player(initial_volume=0.8)
        msg = MediaPlayerCommandRequest(key=1, has_command=True, command=MediaPlayerCommand.MUTE)
        list(entity.handle_message(msg))
        assert entity.volume == 0.0

    def test_mute_saves_previous_volume(self):
        entity = make_media_player(initial_volume=0.8)
        entity.volume = 0.8
        msg = MediaPlayerCommandRequest(key=1, has_command=True, command=MediaPlayerCommand.MUTE)
        list(entity.handle_message(msg))
        assert abs(entity.previous_volume - 0.8) < 0.001

    def test_mute_sets_muted_flag(self):
        entity = make_media_player(key=1)
        msg = MediaPlayerCommandRequest(key=1, has_command=True, command=MediaPlayerCommand.MUTE)
        list(entity.handle_message(msg))
        assert entity.muted is True

    def test_unmute_restores_previous_volume(self):
        entity = make_media_player(key=1, initial_volume=0.8)
        entity.previous_volume = 0.8
        entity.muted = True
        entity.volume = 0.0
        msg = MediaPlayerCommandRequest(key=1, has_command=True, command=MediaPlayerCommand.UNMUTE)
        list(entity.handle_message(msg))
        assert abs(entity.volume - 0.8) < 0.001

    def test_unmute_clears_muted_flag(self):
        entity = make_media_player(key=1)
        entity.muted = True
        msg = MediaPlayerCommandRequest(key=1, has_command=True, command=MediaPlayerCommand.UNMUTE)
        list(entity.handle_message(msg))
        assert entity.muted is False

    def test_double_mute_does_not_overwrite_previous_volume(self):
        entity = make_media_player(key=1, initial_volume=0.9)
        entity.volume = 0.9
        entity.previous_volume = 0.9

        msg_mute = MediaPlayerCommandRequest(key=1, has_command=True, command=MediaPlayerCommand.MUTE)
        list(entity.handle_message(msg_mute))
        # Mute again — should not overwrite previous_volume with 0
        list(entity.handle_message(msg_mute))
        assert abs(entity.previous_volume - 0.9) < 0.001


class TestMediaPlayerEntityPlayback:
    def test_pause_command_calls_music_player_pause(self):
        entity = make_media_player(key=1)
        msg = MediaPlayerCommandRequest(key=1, has_command=True, command=MediaPlayerCommand.PAUSE)
        list(entity.handle_message(msg))
        entity.music_player.pause.assert_called_once()

    def test_stop_command_calls_music_player_stop(self):
        entity = make_media_player(key=1)
        msg = MediaPlayerCommandRequest(key=1, has_command=True, command=MediaPlayerCommand.STOP)
        list(entity.handle_message(msg))
        entity.music_player.stop.assert_called_once()

    def test_play_command_calls_music_player_resume(self):
        entity = make_media_player(key=1)
        msg = MediaPlayerCommandRequest(key=1, has_command=True, command=MediaPlayerCommand.PLAY)
        list(entity.handle_message(msg))
        entity.music_player.resume.assert_called_once()


# ---------------------------------------------------------------------------
# MuteSwitchEntity
# ---------------------------------------------------------------------------


class TestMuteSwitchEntity:
    def test_initial_state_synced_from_get_muted(self):
        entity = make_mute_switch(initial_muted=True)
        assert entity._switch_state is True

    def test_switch_command_calls_set_muted(self):
        entity = make_mute_switch(key=2)
        msg = SwitchCommandRequest(key=2, state=True)
        list(entity.handle_message(msg))
        entity._set_muted.assert_called_once_with(True)

    def test_switch_command_updates_internal_state(self):
        entity = make_mute_switch(key=2)
        msg = SwitchCommandRequest(key=2, state=True)
        list(entity.handle_message(msg))
        assert entity._switch_state is True

    def test_switch_command_yields_switch_state_response(self):
        entity = make_mute_switch(key=2)
        msg = SwitchCommandRequest(key=2, state=True)
        msgs = list(entity.handle_message(msg))
        assert any(isinstance(m, SwitchStateResponse) for m in msgs)

    def test_switch_command_wrong_key_ignored(self):
        entity = make_mute_switch(key=2)
        msg = SwitchCommandRequest(key=99, state=True)
        list(entity.handle_message(msg))
        entity._set_muted.assert_not_called()

    def test_list_entities_request_yields_switch_response(self):
        entity = make_mute_switch()
        msgs = list(entity.handle_message(ListEntitiesRequest()))
        assert any(isinstance(m, ListEntitiesSwitchResponse) for m in msgs)

    def test_subscribe_states_yields_switch_state_response(self):
        entity = make_mute_switch()
        msgs = list(entity.handle_message(SubscribeHomeAssistantStatesRequest()))
        assert any(isinstance(m, SwitchStateResponse) for m in msgs)

    def test_sync_with_state_updates_switch_state(self):
        entity = make_mute_switch(initial_muted=False)
        entity._get_muted.return_value = True
        entity.sync_with_state()
        assert entity._switch_state is True

    def test_update_set_muted_replaces_callback(self):
        entity = make_mute_switch(key=2)
        new_set_muted = MagicMock()
        entity.update_set_muted(new_set_muted)
        msg = SwitchCommandRequest(key=2, state=True)
        list(entity.handle_message(msg))
        new_set_muted.assert_called_once_with(True)

    def test_update_get_muted_replaces_callback(self):
        entity = make_mute_switch()
        new_get_muted = MagicMock(return_value=True)
        entity.update_get_muted(new_get_muted)
        entity.sync_with_state()
        assert entity._switch_state is True


# ---------------------------------------------------------------------------
# MicSettingEntity — number mode (no options)
# ---------------------------------------------------------------------------


class TestMicSettingEntityNumber:
    def test_list_entities_yields_number_response(self):
        entity = make_mic_setting(key=3, options=None)
        msgs = list(entity.handle_message(ListEntitiesRequest()))
        assert any(isinstance(m, ListEntitiesNumberResponse) for m in msgs)

    def test_number_command_calls_set_value(self):
        entity = make_mic_setting(key=3, options=None)
        msg = NumberCommandRequest(key=3, state=5.0)
        list(entity.handle_message(msg))
        entity._set_value_mock.assert_called_once_with(5.0)

    def test_number_command_yields_number_state_response(self):
        entity = make_mic_setting(key=3, options=None)
        msg = NumberCommandRequest(key=3, state=5.0)
        msgs = list(entity.handle_message(msg))
        assert any(isinstance(m, NumberStateResponse) for m in msgs)

    def test_number_command_wrong_key_ignored(self):
        entity = make_mic_setting(key=3, options=None)
        msg = NumberCommandRequest(key=99, state=5.0)
        list(entity.handle_message(msg))
        entity._set_value_mock.assert_not_called()

    def test_subscribe_states_yields_number_state_response(self):
        entity = make_mic_setting(key=3, options=None, value=2.0)
        msgs = list(entity.handle_message(SubscribeHomeAssistantStatesRequest()))
        assert any(isinstance(m, NumberStateResponse) for m in msgs)

    def test_sync_with_state_updates_internal_state(self):
        entity = make_mic_setting(key=3, options=None, value=0.0)
        entity._get_value_mock.return_value = 7.0
        entity.sync_with_state()
        assert entity._state == 7.0


# ---------------------------------------------------------------------------
# MicSettingEntity — select mode (with options)
# ---------------------------------------------------------------------------


class TestMicSettingEntitySelect:
    NOISE_OPTIONS = ["Off", "Low", "Medium", "High", "Max"]

    def test_list_entities_yields_select_response(self):
        entity = make_mic_setting(key=4, options=self.NOISE_OPTIONS)
        msgs = list(entity.handle_message(ListEntitiesRequest()))
        assert any(isinstance(m, ListEntitiesSelectResponse) for m in msgs)

    def test_select_response_has_correct_options(self):
        entity = make_mic_setting(key=4, options=self.NOISE_OPTIONS)
        msgs = list(entity.handle_message(ListEntitiesRequest()))
        select_msg = next(m for m in msgs if isinstance(m, ListEntitiesSelectResponse))
        assert list(select_msg.options) == self.NOISE_OPTIONS

    def test_select_command_calls_set_value(self):
        entity = make_mic_setting(key=4, options=self.NOISE_OPTIONS)
        msg = SelectCommandRequest(key=4, state="High")
        list(entity.handle_message(msg))
        entity._set_value_mock.assert_called_once_with("High")

    def test_select_command_yields_select_state_response(self):
        entity = make_mic_setting(key=4, options=self.NOISE_OPTIONS)
        msg = SelectCommandRequest(key=4, state="Medium")
        msgs = list(entity.handle_message(msg))
        assert any(isinstance(m, SelectStateResponse) for m in msgs)

    def test_select_command_wrong_key_ignored(self):
        entity = make_mic_setting(key=4, options=self.NOISE_OPTIONS)
        msg = SelectCommandRequest(key=99, state="Low")
        list(entity.handle_message(msg))
        entity._set_value_mock.assert_not_called()

    def test_subscribe_states_yields_select_state_response(self):
        entity = make_mic_setting(key=4, options=self.NOISE_OPTIONS, value="Off")
        msgs = list(entity.handle_message(SubscribeHomeAssistantStatesRequest()))
        assert any(isinstance(m, SelectStateResponse) for m in msgs)

    def test_subscribe_states_response_has_string_state(self):
        entity = make_mic_setting(key=4, options=self.NOISE_OPTIONS, value="Low")
        msgs = list(entity.handle_message(SubscribeHomeAssistantStatesRequest()))
        select_msg = next(m for m in msgs if isinstance(m, SelectStateResponse))
        assert isinstance(select_msg.state, str)