"""Unit tests for shared models."""

import json
import pytest
from dataclasses import asdict
from pathlib import Path
from queue import Queue
from unittest.mock import MagicMock, patch


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_preferences(**kwargs):
    from linux_voice_assistant.models import Preferences
    return Preferences(**kwargs)


def make_server_state(tmp_path, **kwargs):
    """Build a minimal ServerState with all required fields mocked out."""
    from linux_voice_assistant.models import Preferences, ServerState

    defaults = dict(
        name="lva-test",
        friendly_name="LVA Test",
        mac_address="aa:bb:cc:dd:ee:ff",
        ip_address="192.168.1.100",
        network_interface="eth0",
        version="1.0.0",
        esphome_version="42.0.0",
        audio_queue=Queue(),
        entities=[],
        available_wake_words={},
        wake_words={},
        active_wake_words=set(),
        stop_word=MagicMock(),
        music_player=MagicMock(),
        tts_player=MagicMock(),
        wakeup_sound="/sounds/wake.flac",
        processing_sound="/sounds/processing.wav",
        timer_finished_sound="/sounds/timer.flac",
        mute_sound="/sounds/mute.flac",
        unmute_sound="/sounds/unmute.flac",
        preferences=Preferences(),
        preferences_path=tmp_path / "preferences.json",
        download_dir=tmp_path / "downloads",
    )
    defaults.update(kwargs)
    return ServerState(**defaults)


# ---------------------------------------------------------------------------
# WakeWordType
# ---------------------------------------------------------------------------


class TestWakeWordType:
    def test_micro_value(self):
        from linux_voice_assistant.models import WakeWordType
        assert WakeWordType.MICRO_WAKE_WORD == "micro"

    def test_open_wake_word_value(self):
        from linux_voice_assistant.models import WakeWordType
        assert WakeWordType.OPEN_WAKE_WORD == "openWakeWord"

    def test_is_string_enum(self):
        from linux_voice_assistant.models import WakeWordType
        assert isinstance(WakeWordType.MICRO_WAKE_WORD, str)


# ---------------------------------------------------------------------------
# Preferences
# ---------------------------------------------------------------------------


class TestPreferences:
    def test_default_active_wake_words_is_empty_list(self):
        p = make_preferences()
        assert p.active_wake_words == []

    def test_default_volume_is_none(self):
        p = make_preferences()
        assert p.volume is None

    def test_default_thinking_sound_is_zero(self):
        p = make_preferences()
        assert p.thinking_sound == 0

    def test_default_mic_auto_gain_is_zero(self):
        p = make_preferences()
        assert p.mic_auto_gain == 0

    def test_default_mic_noise_suppression_is_zero(self):
        p = make_preferences()
        assert p.mic_noise_suppression == 0

    def test_custom_values_stored(self):
        p = make_preferences(
            volume=0.8,
            mic_auto_gain=5,
            mic_noise_suppression=2,
            thinking_sound=1,
        )
        assert p.volume == 0.8
        assert p.mic_auto_gain == 5
        assert p.mic_noise_suppression == 2
        assert p.thinking_sound == 1

    def test_active_wake_words_are_independent_per_instance(self):
        """Mutable default must not be shared between instances."""
        p1 = make_preferences()
        p2 = make_preferences()
        p1.active_wake_words.append("okay_nabu")
        assert p2.active_wake_words == []


# ---------------------------------------------------------------------------
# ServerState.save_preferences()
# ---------------------------------------------------------------------------


class TestSavePreferences:
    def test_creates_preferences_file(self, tmp_path):
        state = make_server_state(tmp_path)
        state.save_preferences()
        assert state.preferences_path.exists()

    def test_saved_json_is_valid(self, tmp_path):
        state = make_server_state(tmp_path)
        state.save_preferences()
        with open(state.preferences_path) as f:
            data = json.load(f)
        assert isinstance(data, dict)

    def test_saved_json_contains_expected_keys(self, tmp_path):
        state = make_server_state(tmp_path)
        state.save_preferences()
        with open(state.preferences_path) as f:
            data = json.load(f)
        assert "mic_auto_gain" in data
        assert "mic_noise_suppression" in data
        assert "volume" in data
        assert "thinking_sound" in data

    def test_saved_values_match_preferences(self, tmp_path):
        from linux_voice_assistant.models import Preferences
        prefs = Preferences(volume=0.75, mic_auto_gain=3, mic_noise_suppression=1)
        state = make_server_state(tmp_path, preferences=prefs)
        state.save_preferences()
        with open(state.preferences_path) as f:
            data = json.load(f)
        assert data["volume"] == 0.75
        assert data["mic_auto_gain"] == 3
        assert data["mic_noise_suppression"] == 1

    def test_creates_parent_directory_if_missing(self, tmp_path):
        nested_path = tmp_path / "nested" / "dir" / "preferences.json"
        state = make_server_state(tmp_path, preferences_path=nested_path)
        state.save_preferences()
        assert nested_path.exists()


# ---------------------------------------------------------------------------
# ServerState.persist_volume()
# ---------------------------------------------------------------------------


class TestPersistVolume:
    def test_updates_volume_on_state(self, tmp_path):
        state = make_server_state(tmp_path)
        state.persist_volume(0.5)
        assert state.volume == 0.5

    def test_updates_volume_on_preferences(self, tmp_path):
        state = make_server_state(tmp_path)
        state.persist_volume(0.5)
        assert state.preferences.volume == 0.5

    def test_clamps_above_one(self, tmp_path):
        state = make_server_state(tmp_path)
        state.persist_volume(1.5)
        assert state.volume == 1.0

    def test_clamps_below_zero(self, tmp_path):
        state = make_server_state(tmp_path)
        state.persist_volume(-0.5)
        assert state.volume == 0.0

    def test_saves_to_file(self, tmp_path):
        state = make_server_state(tmp_path)
        state.persist_volume(0.6)
        with open(state.preferences_path) as f:
            data = json.load(f)
        assert data["volume"] == pytest.approx(0.6)

    def test_skips_save_when_volume_unchanged(self, tmp_path):
        from linux_voice_assistant.models import Preferences
        prefs = Preferences(volume=0.5)
        state = make_server_state(tmp_path, preferences=prefs, volume=0.5)

        with patch.object(state, "save_preferences") as mock_save:
            state.persist_volume(0.5)
            mock_save.assert_not_called()

    def test_saves_when_volume_changed(self, tmp_path):
        from linux_voice_assistant.models import Preferences
        prefs = Preferences(volume=0.5)
        state = make_server_state(tmp_path, preferences=prefs, volume=0.5)

        with patch.object(state, "save_preferences") as mock_save:
            state.persist_volume(0.8)
            mock_save.assert_called_once()

    def test_boundary_value_zero(self, tmp_path):
        state = make_server_state(tmp_path)
        state.persist_volume(0.0)
        assert state.volume == 0.0

    def test_boundary_value_one(self, tmp_path):
        state = make_server_state(tmp_path)
        state.persist_volume(1.0)
        assert state.volume == 1.0


# ---------------------------------------------------------------------------
# ServerState.persist_mic_gain()
# ---------------------------------------------------------------------------


class TestPersistMicGain:
    def test_updates_mic_auto_gain_on_state(self, tmp_path):
        state = make_server_state(tmp_path)
        state.persist_mic_gain(5.0)
        assert state.mic_auto_gain == 5

    def test_updates_mic_auto_gain_on_preferences(self, tmp_path):
        state = make_server_state(tmp_path)
        state.persist_mic_gain(5.0)
        assert state.preferences.mic_auto_gain == 5

    def test_converts_float_to_int(self, tmp_path):
        state = make_server_state(tmp_path)
        state.persist_mic_gain(7.9)
        assert state.mic_auto_gain == 7

    def test_skips_save_when_gain_unchanged(self, tmp_path):
        state = make_server_state(tmp_path)
        state.mic_auto_gain = 3
        state.preferences.mic_auto_gain = 3

        with patch.object(state, "save_preferences") as mock_save:
            state.persist_mic_gain(3.0)
            mock_save.assert_not_called()

    def test_saves_when_gain_changed(self, tmp_path):
        state = make_server_state(tmp_path)
        state.mic_auto_gain = 3
        state.preferences.mic_auto_gain = 3

        with patch.object(state, "save_preferences") as mock_save:
            state.persist_mic_gain(10.0)
            mock_save.assert_called_once()


# ---------------------------------------------------------------------------
# ServerState.persist_mic_noise()
# ---------------------------------------------------------------------------


class TestPersistMicNoise:
    def test_updates_mic_noise_suppression_on_state(self, tmp_path):
        state = make_server_state(tmp_path)
        state.persist_mic_noise(2.0)
        assert state.mic_noise_suppression == 2

    def test_updates_mic_noise_suppression_on_preferences(self, tmp_path):
        state = make_server_state(tmp_path)
        state.persist_mic_noise(2.0)
        assert state.preferences.mic_noise_suppression == 2

    def test_converts_float_to_int(self, tmp_path):
        state = make_server_state(tmp_path)
        state.persist_mic_noise(3.9)
        assert state.mic_noise_suppression == 3

    def test_skips_save_when_noise_unchanged(self, tmp_path):
        state = make_server_state(tmp_path)
        state.mic_noise_suppression = 2
        state.preferences.mic_noise_suppression = 2

        with patch.object(state, "save_preferences") as mock_save:
            state.persist_mic_noise(2.0)
            mock_save.assert_not_called()

    def test_saves_when_noise_changed(self, tmp_path):
        state = make_server_state(tmp_path)
        state.mic_noise_suppression = 2
        state.preferences.mic_noise_suppression = 2

        with patch.object(state, "save_preferences") as mock_save:
            state.persist_mic_noise(4.0)
            mock_save.assert_called_once()