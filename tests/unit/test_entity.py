"""Unit tests for ESPHome entity classes."""

from unittest.mock import MagicMock

import pytest
from aioesphomeapi.api_pb2 import (  # type: ignore[attr-defined]
    ListEntitiesMediaPlayerResponse,
    ListEntitiesNumberResponse,
    ListEntitiesRequest,
    ListEntitiesSelectResponse,
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


class TestMediaPlayerEntityAnnouncement:
    @staticmethod
    def _announce(entity):
        msgs = list(entity.play("http://example.com/tts.mp3", announcement=True))
        done_callback = entity.announce_player.play.call_args.kwargs["done_callback"]
        return msgs, done_callback

    def test_playing_music_is_ducked_and_unducked(self):
        entity = make_media_player()
        entity.music_player.is_paused = False
        entity.music_player.is_playing = True

        msgs, done_callback = self._announce(entity)
        entity.music_player.duck.assert_called_once_with(owner="media_announcement")
        entity.music_player.pause.assert_not_called()
        assert msgs[-1].state == MediaPlayerState.ANNOUNCING

        done_callback()
        entity.music_player.unduck.assert_called_once_with(owner="media_announcement")
        entity.music_player.resume.assert_not_called()
        assert entity.state == MediaPlayerState.PLAYING

    def test_paused_music_stays_paused(self):
        entity = make_media_player()
        entity.music_player.is_paused = True
        entity.music_player.is_playing = True

        _, done_callback = self._announce(entity)
        done_callback()

        entity.music_player.duck.assert_not_called()
        entity.music_player.resume.assert_not_called()
        assert entity.state == MediaPlayerState.PAUSED

    def test_idle_music_reports_idle_after_announcement(self):
        entity = make_media_player()
        entity.music_player.is_paused = False
        entity.music_player.is_playing = False

        _, done_callback = self._announce(entity)
        done_callback()

        entity.music_player.resume.assert_not_called()
        assert entity.state == MediaPlayerState.IDLE


class TestMediaPlayerEntitySendspin:
    @staticmethod
    def _make(sendspin_state="playing", volume=1.0):
        from linux_voice_assistant.sendspin_player import SendspinPlayer

        entity = make_media_player(key=1, initial_volume=volume)
        entity.sendspin_player = MagicMock(spec=SendspinPlayer)
        entity.sendspin_player.is_ducked = False
        entity.music_player.is_playing = False
        entity.music_player.is_paused = False
        entity._sendspin_state = sendspin_state  # pylint: disable=protected-access
        return entity

    @staticmethod
    def _status(state="playing", volume=100, muted=False):
        from linux_voice_assistant.sendspin_player import SendspinStatus

        return SendspinStatus(state=state, volume=volume, muted=muted)

    def test_status_is_mirrored_and_broadcast(self):
        entity = self._make(sendspin_state="idle")
        entity.on_sendspin_status(self._status("playing"))
        assert entity.state == MediaPlayerState.PLAYING
        entity.server.state.broadcast.assert_called_once()

        entity.on_sendspin_status(self._status("idle"))
        assert entity.state == MediaPlayerState.IDLE

        entity.on_sendspin_status(self._status("playing"))
        entity.on_sendspin_status(None)
        assert entity.state == MediaPlayerState.IDLE

    def test_own_music_has_priority_over_sendspin(self):
        entity = self._make(sendspin_state="idle")
        entity.music_player.is_playing = True
        entity.on_sendspin_status(self._status("playing"))
        assert entity.state == MediaPlayerState.IDLE

    def test_pause_and_stop_go_to_sendspin_while_it_plays(self):
        entity = self._make("playing")
        list(entity.handle_message(MediaPlayerCommandRequest(key=1, has_command=True, command=MediaPlayerCommand.PAUSE)))
        entity.sendspin_player.pause_playback.assert_called_once()
        entity.music_player.pause.assert_not_called()
        assert entity.state == MediaPlayerState.IDLE

        entity = self._make("playing")
        list(entity.handle_message(MediaPlayerCommandRequest(key=1, has_command=True, command=MediaPlayerCommand.STOP)))
        entity.sendspin_player.stop.assert_called_once()
        entity.music_player.stop.assert_not_called()
        assert entity.state == MediaPlayerState.IDLE

    def test_play_goes_to_sendspin_when_lva_has_nothing_to_resume(self):
        entity = self._make("idle")
        list(entity.handle_message(MediaPlayerCommandRequest(key=1, has_command=True, command=MediaPlayerCommand.PLAY)))
        entity.sendspin_player.play.assert_called_once()
        entity.music_player.resume.assert_not_called()

    def test_play_resumes_paused_lva_music(self):
        entity = self._make("idle")
        entity.music_player.is_paused = True
        list(entity.handle_message(MediaPlayerCommandRequest(key=1, has_command=True, command=MediaPlayerCommand.PLAY)))
        entity.music_player.resume.assert_called_once()
        entity.sendspin_player.play.assert_not_called()

    def test_commands_go_to_mpv_when_sendspin_is_idle(self):
        entity = self._make("idle")
        entity.music_player.is_playing = True
        list(entity.handle_message(MediaPlayerCommandRequest(key=1, has_command=True, command=MediaPlayerCommand.PAUSE)))
        entity.music_player.pause.assert_called_once()
        entity.sendspin_player.pause_playback.assert_not_called()

    def test_volume_command_is_forwarded(self):
        entity = self._make()
        list(entity.handle_message(MediaPlayerCommandRequest(key=1, has_volume=True, volume=0.4)))
        entity.sendspin_player.set_volume.assert_called_once_with(40)

    def test_mute_command_is_forwarded(self):
        entity = self._make()
        list(entity.handle_message(MediaPlayerCommandRequest(key=1, has_command=True, command=MediaPlayerCommand.MUTE)))
        entity.sendspin_player.set_muted.assert_called_once_with(True)

    def test_volume_follows_sendspin(self):
        entity = self._make(volume=1.0)
        entity.on_sendspin_status(self._status("playing", volume=30))
        assert entity.volume == 0.3
        entity.music_player.set_volume.assert_called_with(30)
        entity.sendspin_player.set_volume.assert_not_called()

    def test_volume_is_not_taken_over_while_ducked_or_just_set(self):
        entity = self._make(volume=1.0)
        entity.sendspin_player.is_ducked = True
        entity.on_sendspin_status(self._status("playing", volume=50))
        assert entity.volume == 1.0

        entity.sendspin_player.is_ducked = False
        list(entity.handle_message(MediaPlayerCommandRequest(key=1, has_volume=True, volume=0.8)))
        entity.on_sendspin_status(self._status("playing", volume=50))
        assert entity.volume == pytest.approx(0.8)

    def test_sendspin_starting_stops_lva_music(self):
        entity = self._make("idle")
        entity.music_player.is_playing = True
        entity.on_sendspin_status(self._status("playing"))
        entity.music_player.stop.assert_called_once()

        entity.music_player.stop.reset_mock()
        entity.on_sendspin_status(self._status("playing"))
        entity.music_player.stop.assert_not_called()

    def test_paused_sendspin_counts_as_idle(self):
        entity = self._make("idle")
        entity.on_sendspin_status(self._status("idle"))
        assert entity.state == MediaPlayerState.IDLE
        assert not entity._sendspin_is_active()  # pylint: disable=protected-access

    def test_stale_sendspin_status_after_music_start_is_ignored(self):
        entity = self._make("playing")
        entity.sendspin_player.status.return_value = self._status("playing")
        list(entity.play("http://example.com/song.mp3"))
        entity.music_player.is_playing = True

        entity.on_sendspin_status(self._status("playing"))
        entity.music_player.stop.assert_not_called()

    def test_sendspin_playing_is_announced_to_peripherals(self):
        entity = self._make("idle")
        satellite = entity.server.state.satellite
        entity.on_sendspin_status(self._status("playing"))
        satellite.emit_media_playing.assert_called_once_with(during_conversation=False)

        entity.on_sendspin_status(self._status("paused"))
        entity.on_sendspin_status(self._status("playing"))
        assert satellite.emit_media_playing.call_count == 2

    def test_sendspin_playing_stays_quiet_when_state_is_unchanged(self):
        entity = self._make("idle")
        entity.on_sendspin_status(self._status("playing"))
        entity.server.state.satellite.emit_media_playing.reset_mock()
        entity.on_sendspin_status(self._status("playing"))
        entity.server.state.satellite.emit_media_playing.assert_not_called()

    def test_music_from_home_assistant_stops_sendspin(self):
        entity = self._make("playing")
        entity.sendspin_player.status.return_value = self._status("playing")
        list(entity.play("http://example.com/song.mp3"))
        entity.sendspin_player.stop.assert_called_once()
        entity.music_player.play.assert_called_once()

    def test_music_leaves_idle_sendspin_alone(self):
        entity = self._make("idle")
        entity.sendspin_player.status.return_value = self._status("idle")
        list(entity.play("http://example.com/song.mp3"))
        entity.sendspin_player.stop.assert_not_called()

    def test_announcement_does_not_stop_sendspin(self):
        entity = self._make("playing")
        entity.sendspin_player.status.return_value = self._status("playing")
        list(entity.play("http://example.com/tts.mp3", announcement=True))
        entity.sendspin_player.stop.assert_not_called()

    def test_sendspin_uri_current_resumes_and_others_are_ignored(self):
        entity = self._make("paused")
        list(entity.handle_message(MediaPlayerCommandRequest(key=1, has_media_url=True, media_url="sendspin://current")))
        entity.sendspin_player.play.assert_called_once()

        list(entity.handle_message(MediaPlayerCommandRequest(key=1, has_media_url=True, media_url="sendspin://192.168.1.2:8927")))
        entity.music_player.play.assert_not_called()
        entity.sendspin_player.play.assert_called_once()


class TestMediaPlayerEntitySendspinAnnouncement:
    _make = staticmethod(TestMediaPlayerEntitySendspin._make)  # pylint: disable=protected-access
    _status = staticmethod(TestMediaPlayerEntitySendspin._status)  # pylint: disable=protected-access

    def _announce(self, entity):
        msgs = list(entity.play("http://example.com/tts.mp3", announcement=True))
        return msgs, entity.announce_player.play.call_args.kwargs["done_callback"]

    def test_announcement_ducks_sendspin_and_reports_announcing(self):
        entity = self._make("playing")

        msgs, done_callback = self._announce(entity)
        entity.sendspin_player.duck.assert_called_once_with(owner="media_announcement")
        assert msgs[-1].state == MediaPlayerState.ANNOUNCING
        entity.sendspin_player.stop.assert_not_called()

        entity.on_sendspin_status(self._status("playing"))
        assert entity.state == MediaPlayerState.ANNOUNCING

        done_callback()
        entity.sendspin_player.unduck.assert_called_once_with(owner="media_announcement")
        assert entity.state == MediaPlayerState.PLAYING

    def test_announcement_over_stopped_sendspin_ends_idle(self):
        entity = self._make("idle")
        _, done_callback = self._announce(entity)
        done_callback()
        assert entity.state == MediaPlayerState.IDLE


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
