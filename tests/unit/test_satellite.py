"""Unit tests for VoiceSatelliteProtocol logic."""

import pytest
from unittest.mock import MagicMock, patch, call
from queue import Queue
from pathlib import Path


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_state(tmp_path=None):
    """Build a minimal ServerState with all fields mocked."""
    from linux_voice_assistant.models import Preferences, ServerState, WakeWordType, AvailableWakeWord

    if tmp_path is None:
        import tempfile
        tmp_path = Path(tempfile.mkdtemp())

    stop_word = MagicMock()
    stop_word.id = "stop"
    stop_word.is_active = False

    prefs = Preferences()

    state = ServerState(
        name="lva-test",
        friendly_name="LVA Test",
        mac_address="aa:bb:cc:dd:ee:ff",
        ip_address="192.168.1.1",
        network_interface="eth0",
        version="1.0.0",
        esphome_version="42.0.0",
        audio_queue=Queue(),
        entities=[],
        available_wake_words={},
        wake_words={},
        active_wake_words=set(),
        stop_word=stop_word,
        music_player=MagicMock(),
        tts_player=MagicMock(),
        wakeup_sound="/sounds/wake.flac",
        processing_sound="/sounds/processing.wav",
        timer_finished_sound="/sounds/timer.flac",
        mute_sound="/sounds/mute.flac",
        unmute_sound="/sounds/unmute.flac",
        preferences=prefs,
        preferences_path=tmp_path / "preferences.json",
        download_dir=tmp_path / "downloads",
        volume=1.0,
        mic_volume=100,
        mic_auto_gain=0,
        mic_noise_suppression=0,
    )
    return state


def make_satellite(tmp_path=None):
    """Build a VoiceSatelliteProtocol with all heavy dependencies mocked."""
    state = make_state(tmp_path)

    # Mock the sensitivity entity classes so __init__ doesn't blow up
    with patch("linux_voice_assistant.satellite.WakeWord1SensitivityNumberEntity", MagicMock()), \
         patch("linux_voice_assistant.satellite.WakeWord2SensitivityNumberEntity", MagicMock()), \
         patch("linux_voice_assistant.satellite.StopWordSensitivityNumberEntity", MagicMock()):
        from linux_voice_assistant.satellite import VoiceSatelliteProtocol
        satellite = VoiceSatelliteProtocol(state)

    # Attach mock transport so send_messages works
    satellite._writelines = MagicMock()
    satellite._loop = None
    return satellite


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
        from linux_voice_assistant.models import Preferences
        state = make_state(tmp_path)
        state.preferences.thinking_sound = 1

        with patch("linux_voice_assistant.satellite.WakeWord1SensitivityNumberEntity", MagicMock()), \
             patch("linux_voice_assistant.satellite.WakeWord2SensitivityNumberEntity", MagicMock()), \
             patch("linux_voice_assistant.satellite.StopWordSensitivityNumberEntity", MagicMock()):
            from linux_voice_assistant.satellite import VoiceSatelliteProtocol
            sat = VoiceSatelliteProtocol(state)

        assert sat.state.thinking_sound_enabled is True

    def test_output_only_sets_limited_features(self, tmp_path):
        from aioesphomeapi.model import VoiceAssistantFeature
        state = make_state(tmp_path)
        state.output_only = True

        with patch("linux_voice_assistant.satellite.WakeWord1SensitivityNumberEntity", MagicMock()), \
             patch("linux_voice_assistant.satellite.WakeWord2SensitivityNumberEntity", MagicMock()), \
             patch("linux_voice_assistant.satellite.StopWordSensitivityNumberEntity", MagicMock()):
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