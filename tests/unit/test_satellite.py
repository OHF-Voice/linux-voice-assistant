"""Unit tests for VoiceSatelliteProtocol logic."""

from unittest.mock import MagicMock, patch

import pytest
from aioesphomeapi.model import VoiceAssistantTimerEventType

from tests.unit.conftest import make_satellite, make_state

# ---------------------------------------------------------------------------
# Initialization
# ---------------------------------------------------------------------------


class TestInit:
    def test_satellite_stored_on_state(self, tmp_path):
        sat = make_satellite(tmp_path)
        assert sat.state.satellite is sat

    def test_connected_starts_false(self, tmp_path):
        sat = make_satellite(tmp_path)
        assert sat.state.connected is False

    def test_media_player_entity_created(self, tmp_path):
        from linux_voice_assistant.entity import MediaPlayerEntity

        sat = make_satellite(tmp_path)
        assert sat.state.media_player_entity is not None
        assert isinstance(sat.state.media_player_entity, MediaPlayerEntity)

    def test_mute_switch_entity_created(self, tmp_path):
        from linux_voice_assistant.entity import MuteSwitchEntity

        sat = make_satellite(tmp_path)
        assert sat.state.mute_switch_entity is not None
        assert isinstance(sat.state.mute_switch_entity, MuteSwitchEntity)

    def test_thinking_sound_entity_created(self, tmp_path):
        from linux_voice_assistant.entity import ThinkingSoundEntity

        sat = make_satellite(tmp_path)
        assert sat.state.thinking_sound_entity is not None
        assert isinstance(sat.state.thinking_sound_entity, ThinkingSoundEntity)

    def test_mic_gain_entity_created(self, tmp_path):
        from linux_voice_assistant.entity import MicSettingEntity

        sat = make_satellite(tmp_path)
        assert sat.state.mic_gain_entity is not None
        assert isinstance(sat.state.mic_gain_entity, MicSettingEntity)

    def test_mic_noise_entity_created(self, tmp_path):
        from linux_voice_assistant.entity import MicSettingEntity

        sat = make_satellite(tmp_path)
        assert sat.state.mic_noise_suppression_entity is not None
        assert isinstance(sat.state.mic_noise_suppression_entity, MicSettingEntity)

    def test_mic_volume_entity_created(self, tmp_path):
        from linux_voice_assistant.entity import MicSettingEntity

        sat = make_satellite(tmp_path)
        assert sat.state.mic_volume_entity is not None
        assert isinstance(sat.state.mic_volume_entity, MicSettingEntity)

    def test_pipeline_not_active_on_start(self, tmp_path):
        sat = make_satellite(tmp_path)
        assert sat._pipeline_active is False

    def test_not_streaming_audio_on_start(self, tmp_path):
        sat = make_satellite(tmp_path)
        assert sat._is_streaming_audio is False

    def test_not_muted_on_start(self, tmp_path):
        sat = make_satellite(tmp_path)
        assert sat.state.muted is False

    def test_thinking_sound_loaded_from_preferences(self, tmp_path):
        state = make_state(tmp_path)
        state.preferences.thinking_sound = 1

        with (
            patch("linux_voice_assistant.satellite.WakeWord1SensitivityNumberEntity", MagicMock()),
            patch("linux_voice_assistant.satellite.WakeWord2SensitivityNumberEntity", MagicMock()),
            patch("linux_voice_assistant.satellite.StopWordSensitivityNumberEntity", MagicMock()),
        ):
            from linux_voice_assistant.satellite import VoiceSatelliteProtocol

            sat = VoiceSatelliteProtocol(state)

        assert sat.state.thinking_sound_enabled is True

    def test_output_only_sets_limited_features(self, tmp_path):
        from aioesphomeapi.model import VoiceAssistantFeature

        state = make_state(tmp_path)
        state.output_only = True

        with (
            patch("linux_voice_assistant.satellite.WakeWord1SensitivityNumberEntity", MagicMock()),
            patch("linux_voice_assistant.satellite.WakeWord2SensitivityNumberEntity", MagicMock()),
            patch("linux_voice_assistant.satellite.StopWordSensitivityNumberEntity", MagicMock()),
        ):
            from linux_voice_assistant.satellite import VoiceSatelliteProtocol

            sat = VoiceSatelliteProtocol(state)

        assert sat.supported_features & VoiceAssistantFeature.VOICE_ASSISTANT == 0


# ---------------------------------------------------------------------------
# _set_muted()
# ---------------------------------------------------------------------------


class TestSetMuted:
    def test_muting_sets_muted_flag(self, tmp_path):
        sat = make_satellite(tmp_path)
        sat._set_muted(True)
        assert sat.state.muted is True

    def test_unmuting_clears_muted_flag(self, tmp_path):
        sat = make_satellite(tmp_path)
        sat.state.muted = True
        sat._set_muted(False)
        assert sat.state.muted is False

    def test_muting_stops_tts_player(self, tmp_path):
        sat = make_satellite(tmp_path)
        sat._set_muted(True)
        sat.state.tts_player.stop.assert_called()

    def test_muting_plays_mute_sound(self, tmp_path):
        sat = make_satellite(tmp_path)
        sat._set_muted(True)
        sat.state.tts_player.play.assert_called_with(sat.state.mute_sound)

    def test_unmuting_plays_unmute_sound(self, tmp_path):
        sat = make_satellite(tmp_path)
        sat._set_muted(False)
        sat.state.tts_player.play.assert_called_with(sat.state.unmute_sound)

    def test_muting_stops_audio_streaming(self, tmp_path):
        sat = make_satellite(tmp_path)
        sat._is_streaming_audio = True
        sat._set_muted(True)
        assert sat._is_streaming_audio is False


# ---------------------------------------------------------------------------
# _set_thinking_sound_enabled()
# ---------------------------------------------------------------------------


class TestSetThinkingSoundEnabled:
    def test_enables_thinking_sound(self, tmp_path):
        sat = make_satellite(tmp_path)
        sat._set_thinking_sound_enabled(True)
        assert sat.state.thinking_sound_enabled is True

    def test_disables_thinking_sound(self, tmp_path):
        sat = make_satellite(tmp_path)
        sat.state.thinking_sound_enabled = True
        sat._set_thinking_sound_enabled(False)
        assert sat.state.thinking_sound_enabled is False

    def test_updates_preferences(self, tmp_path):
        sat = make_satellite(tmp_path)
        sat._set_thinking_sound_enabled(True)
        assert sat.state.preferences.thinking_sound == 1

    def test_saves_preferences_to_file(self, tmp_path):
        sat = make_satellite(tmp_path)
        sat._set_thinking_sound_enabled(True)
        assert sat.state.preferences_path.exists()


# ---------------------------------------------------------------------------
# _set_sensitivity_1/2 and _set_stop_sensitivity
# ---------------------------------------------------------------------------


class TestSensitivitySetters:
    def test_set_sensitivity_1_updates_threshold(self, tmp_path):
        sat = make_satellite(tmp_path)
        sat._set_sensitivity_1(0.85)
        assert sat.state.wake_word_1_threshold == pytest.approx(0.85)

    def test_set_sensitivity_1_updates_preferences(self, tmp_path):
        sat = make_satellite(tmp_path)
        sat._set_sensitivity_1(0.85)
        assert sat.state.preferences.wake_word_1_sensitivity == pytest.approx(0.85)

    def test_set_sensitivity_2_updates_threshold(self, tmp_path):
        sat = make_satellite(tmp_path)
        sat._set_sensitivity_2(0.6)
        assert sat.state.wake_word_2_threshold == pytest.approx(0.6)

    def test_set_sensitivity_2_updates_preferences(self, tmp_path):
        sat = make_satellite(tmp_path)
        sat._set_sensitivity_2(0.6)
        assert sat.state.preferences.wake_word_2_sensitivity == pytest.approx(0.6)

    def test_set_stop_sensitivity_updates_threshold(self, tmp_path):
        sat = make_satellite(tmp_path)
        sat._set_stop_sensitivity(0.5)
        assert sat.state.stop_word_threshold == pytest.approx(0.5)

    def test_set_stop_sensitivity_updates_preferences(self, tmp_path):
        sat = make_satellite(tmp_path)
        sat._set_stop_sensitivity(0.5)
        assert sat.state.preferences.stop_word_sensitivity == pytest.approx(0.5)

    def test_sensitivity_saves_preferences(self, tmp_path):
        sat = make_satellite(tmp_path)
        sat._set_sensitivity_1(0.9)
        assert sat.state.preferences_path.exists()


# ---------------------------------------------------------------------------
# handle_audio()
# ---------------------------------------------------------------------------


class TestHandleAudio:
    def test_does_not_send_when_not_streaming(self, tmp_path):
        sat = make_satellite(tmp_path)
        sat._is_streaming_audio = False
        sat.handle_audio(b"\x00" * 320)
        sat._writelines.assert_not_called()

    def test_does_not_send_when_muted(self, tmp_path):
        sat = make_satellite(tmp_path)
        sat._is_streaming_audio = True
        sat.state.muted = True
        sat.handle_audio(b"\x00" * 320)
        sat._writelines.assert_not_called()

    def test_sends_when_streaming_and_not_muted(self, tmp_path):
        sat = make_satellite(tmp_path)
        sat._is_streaming_audio = True
        sat.state.muted = False
        sat._loop = None
        sat.handle_audio(b"\x00" * 320)
        sat._writelines.assert_called()


# ---------------------------------------------------------------------------
# play_tts()
# ---------------------------------------------------------------------------


class TestPlayTts:
    def test_does_not_play_when_no_url(self, tmp_path):
        sat = make_satellite(tmp_path)
        sat._tts_url = None
        sat.play_tts()
        sat.state.tts_player.play.assert_not_called()

    def test_does_not_play_when_already_played(self, tmp_path):
        sat = make_satellite(tmp_path)
        sat._tts_url = "http://example.com/tts.mp3"
        sat._tts_played = True
        sat.play_tts()
        sat.state.tts_player.play.assert_not_called()

    def test_plays_tts_url(self, tmp_path):
        sat = make_satellite(tmp_path)
        sat._tts_url = "http://example.com/tts.mp3"
        sat._tts_played = False
        sat.play_tts()
        sat.state.tts_player.play.assert_called_once()
        args, _ = sat.state.tts_player.play.call_args
        assert args[0] == "http://example.com/tts.mp3"

    def test_sets_tts_played_flag(self, tmp_path):
        sat = make_satellite(tmp_path)
        sat._tts_url = "http://example.com/tts.mp3"
        sat._tts_played = False
        sat.play_tts()
        assert sat._tts_played is True

    def test_adds_stop_word_to_active_wake_words(self, tmp_path):
        sat = make_satellite(tmp_path)
        sat._tts_url = "http://example.com/tts.mp3"
        sat._tts_played = False
        sat.play_tts()
        assert sat.state.stop_word.id in sat.state.active_wake_words


# ---------------------------------------------------------------------------
# stop()
# ---------------------------------------------------------------------------


class TestStop:
    def test_stop_clears_pipeline_active(self, tmp_path):
        sat = make_satellite(tmp_path)
        sat._pipeline_active = True
        sat.stop()
        assert sat._pipeline_active is False

    def test_stop_discards_stop_word_from_active(self, tmp_path):
        sat = make_satellite(tmp_path)
        sat.state.active_wake_words.add(sat.state.stop_word.id)
        sat.stop()
        assert sat.state.stop_word.id not in sat.state.active_wake_words

    def test_stop_calls_tts_player_stop(self, tmp_path):
        sat = make_satellite(tmp_path)
        sat._timer_finished = False
        sat.stop()
        sat.state.tts_player.stop.assert_called()


# ---------------------------------------------------------------------------
# duck() / unduck()
# ---------------------------------------------------------------------------


class TestDuckUnduck:
    def test_duck_calls_music_player_duck(self, tmp_path):
        sat = make_satellite(tmp_path)
        sat.duck()
        sat.state.music_player.duck.assert_called_once()

    def test_unduck_calls_music_player_unduck(self, tmp_path):
        sat = make_satellite(tmp_path)
        sat.unduck()
        sat.state.music_player.unduck.assert_called_once()


# ---------------------------------------------------------------------------
# connection_lost()
# ---------------------------------------------------------------------------


class TestConnectionLost:
    def test_connection_lost_clears_connected_flag(self, tmp_path):
        sat = make_satellite(tmp_path)
        sat.state.connected = True
        sat.connection_lost(None)
        assert sat.state.connected is False

    def test_connection_lost_clears_satellite_reference(self, tmp_path):
        sat = make_satellite(tmp_path)
        sat.connection_lost(None)
        assert sat.state.satellite is None

    def test_connection_lost_stops_streaming(self, tmp_path):
        sat = make_satellite(tmp_path)
        sat._is_streaming_audio = True
        sat.connection_lost(None)
        assert sat._is_streaming_audio is False

    def test_connection_lost_clears_pipeline_active(self, tmp_path):
        sat = make_satellite(tmp_path)
        sat._pipeline_active = True
        sat.connection_lost(None)
        assert sat._pipeline_active is False

    def test_connection_lost_stops_music_player(self, tmp_path):
        sat = make_satellite(tmp_path)
        sat.connection_lost(None)
        sat.state.music_player.stop.assert_called()

    def test_connection_lost_stops_tts_player(self, tmp_path):
        sat = make_satellite(tmp_path)
        sat.connection_lost(None)
        sat.state.tts_player.stop.assert_called()


# ---------------------------------------------------------------------------
# Timer sensor entities (issue #130 - expose timer values to Home Assistant)
# ---------------------------------------------------------------------------


class TestTimerEntitiesCreated:
    def test_timer_seconds_left_entity_created(self, tmp_path):
        from linux_voice_assistant.entity import TimerSecondsLeftSensorEntity

        sat = make_satellite(tmp_path)
        assert sat.state.timer_seconds_left_entity is not None
        assert isinstance(sat.state.timer_seconds_left_entity, TimerSecondsLeftSensorEntity)

    def test_timer_total_seconds_entity_created(self, tmp_path):
        from linux_voice_assistant.entity import TimerTotalSecondsSensorEntity

        sat = make_satellite(tmp_path)
        assert sat.state.timer_total_seconds_entity is not None
        assert isinstance(sat.state.timer_total_seconds_entity, TimerTotalSecondsSensorEntity)

    def test_timer_name_entity_created(self, tmp_path):
        from linux_voice_assistant.entity import TimerNameTextSensorEntity

        sat = make_satellite(tmp_path)
        assert sat.state.timer_name_entity is not None
        assert isinstance(sat.state.timer_name_entity, TimerNameTextSensorEntity)

    def test_timer_entities_registered_in_entities_list(self, tmp_path):
        sat = make_satellite(tmp_path)
        assert sat.state.timer_seconds_left_entity in sat.state.entities
        assert sat.state.timer_total_seconds_entity in sat.state.entities
        assert sat.state.timer_name_entity in sat.state.entities

    def test_timer_entities_survive_reinit_without_duplication(self, tmp_path):
        """Re-running __init__ (e.g. an HA reconnect) must reuse the same
        entities rather than creating duplicates in state.entities."""
        sat = make_satellite(tmp_path)
        first = sat.state.timer_seconds_left_entity
        count_before = len(sat.state.entities)

        from linux_voice_assistant.satellite import VoiceSatelliteProtocol

        VoiceSatelliteProtocol.__init__(sat, sat.state)

        assert sat.state.timer_seconds_left_entity is first
        assert len(sat.state.entities) == count_before


class TestHandleTimerEvent:
    """Verify handle_timer_event() keeps the three HA sensors in sync."""

    @staticmethod
    def _make_timer_msg(event_type, timer_id="t1", name="Pasta", total_seconds=600, seconds_left=598):
        from aioesphomeapi.api_pb2 import VoiceAssistantTimerEventResponse

        return VoiceAssistantTimerEventResponse(
            event_type=event_type,
            timer_id=timer_id,
            name=name,
            total_seconds=total_seconds,
            seconds_left=seconds_left,
        )

    def test_timer_started_updates_sensor_values(self, tmp_path):
        sat = make_satellite(tmp_path)
        msg = self._make_timer_msg(
            VoiceAssistantTimerEventType.VOICE_ASSISTANT_TIMER_STARTED,
            name="Pasta",
            total_seconds=600,
            seconds_left=598,
        )

        sat.handle_timer_event(VoiceAssistantTimerEventType.VOICE_ASSISTANT_TIMER_STARTED, msg)

        assert sat.state.timer_name_entity.timer_name == "Pasta"
        assert sat.state.timer_total_seconds_entity.total_seconds == 600
        assert sat.state.timer_seconds_left_entity.seconds_left == 598

    def test_timer_started_pushes_state_messages(self, tmp_path):
        sat = make_satellite(tmp_path)
        sat.send_messages = MagicMock()
        msg = self._make_timer_msg(VoiceAssistantTimerEventType.VOICE_ASSISTANT_TIMER_STARTED)

        sat.handle_timer_event(VoiceAssistantTimerEventType.VOICE_ASSISTANT_TIMER_STARTED, msg)

        sat.send_messages.assert_called_once()
        (sent_messages,), _ = sat.send_messages.call_args
        assert len(sent_messages) == 3

    def test_timer_updated_refreshes_seconds_left(self, tmp_path):
        sat = make_satellite(tmp_path)
        started = self._make_timer_msg(VoiceAssistantTimerEventType.VOICE_ASSISTANT_TIMER_STARTED, seconds_left=598)
        sat.handle_timer_event(VoiceAssistantTimerEventType.VOICE_ASSISTANT_TIMER_STARTED, started)

        updated = self._make_timer_msg(VoiceAssistantTimerEventType.VOICE_ASSISTANT_TIMER_UPDATED, seconds_left=550)
        sat.handle_timer_event(VoiceAssistantTimerEventType.VOICE_ASSISTANT_TIMER_UPDATED, updated)

        assert sat.state.timer_seconds_left_entity.seconds_left == 550

    def test_timer_cancelled_resets_sensors(self, tmp_path):
        sat = make_satellite(tmp_path)
        started = self._make_timer_msg(VoiceAssistantTimerEventType.VOICE_ASSISTANT_TIMER_STARTED)
        sat.handle_timer_event(VoiceAssistantTimerEventType.VOICE_ASSISTANT_TIMER_STARTED, started)

        cancelled = self._make_timer_msg(VoiceAssistantTimerEventType.VOICE_ASSISTANT_TIMER_CANCELLED)
        sat.handle_timer_event(VoiceAssistantTimerEventType.VOICE_ASSISTANT_TIMER_CANCELLED, cancelled)

        assert sat.state.timer_name_entity.timer_name == ""
        assert sat.state.timer_total_seconds_entity.total_seconds == 0
        assert sat.state.timer_seconds_left_entity.seconds_left == 0

    def test_timer_finished_resets_sensors(self, tmp_path):
        sat = make_satellite(tmp_path)
        started = self._make_timer_msg(VoiceAssistantTimerEventType.VOICE_ASSISTANT_TIMER_STARTED)
        sat.handle_timer_event(VoiceAssistantTimerEventType.VOICE_ASSISTANT_TIMER_STARTED, started)

        finished = self._make_timer_msg(VoiceAssistantTimerEventType.VOICE_ASSISTANT_TIMER_FINISHED)
        sat.handle_timer_event(VoiceAssistantTimerEventType.VOICE_ASSISTANT_TIMER_FINISHED, finished)

        assert sat.state.timer_seconds_left_entity.seconds_left == 0

    def test_handles_missing_timer_entities_gracefully(self, tmp_path):
        """If a timer entity is somehow absent (e.g. very old preferences
        state), handle_timer_event must not raise."""
        sat = make_satellite(tmp_path)
        sat.state.timer_seconds_left_entity = None
        sat.state.timer_total_seconds_entity = None
        sat.state.timer_name_entity = None
        sat.send_messages = MagicMock()

        msg = self._make_timer_msg(VoiceAssistantTimerEventType.VOICE_ASSISTANT_TIMER_STARTED)
        sat.handle_timer_event(VoiceAssistantTimerEventType.VOICE_ASSISTANT_TIMER_STARTED, msg)

        sat.send_messages.assert_not_called()
