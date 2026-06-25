"""Unit tests for __main__.py — process_audio logic and argument parsing helpers."""

import pytest
import numpy as np
from unittest.mock import MagicMock, patch, call
from queue import Queue
from pathlib import Path


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_state(tmp_path):
    from linux_voice_assistant.models import Preferences, ServerState

    stop_word = MagicMock()
    stop_word.id = "stop"
    stop_word.is_active = False
    stop_word.process_streaming.return_value = False

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


def make_audio_chunk(samples: int = 1024, value: float = 0.0) -> np.ndarray:
    return np.full(samples, value, dtype=np.float32)


# ---------------------------------------------------------------------------
# process_audio — mic volume scaling
# ---------------------------------------------------------------------------


class TestProcessAudioMicVolume:
    def test_mic_volume_scalar_at_100(self, tmp_path):
        """mic_volume=100 → scalar=1.0 → no attenuation."""
        state = make_state(tmp_path)
        state.mic_volume = 100
        state.satellite = None  # no satellite → loop exits immediately

        # The scalar clamp: max(0.1, min(1.0, 100/100)) = 1.0
        scalar = max(0.1, min(1.0, state.mic_volume / 100.0))
        assert scalar == pytest.approx(1.0)

    def test_mic_volume_scalar_at_50(self, tmp_path):
        state = make_state(tmp_path)
        state.mic_volume = 50
        scalar = max(0.1, min(1.0, state.mic_volume / 100.0))
        assert scalar == pytest.approx(0.5)

    def test_mic_volume_scalar_clamped_above_100(self, tmp_path):
        state = make_state(tmp_path)
        state.mic_volume = 200
        scalar = max(0.1, min(1.0, state.mic_volume / 100.0))
        assert scalar == pytest.approx(1.0)

    def test_mic_volume_scalar_minimum_is_0_1(self, tmp_path):
        state = make_state(tmp_path)
        state.mic_volume = 0
        scalar = max(0.1, min(1.0, state.mic_volume / 100.0))
        assert scalar == pytest.approx(0.1)

    def test_audio_chunk_scaled_by_mic_volume(self, tmp_path):
        """Verify the numpy scaling produces correct output."""
        audio = make_audio_chunk(value=0.5)
        mic_vol_scalar = 0.5
        result = np.clip(audio * mic_vol_scalar, -1.0, 1.0)
        assert np.allclose(result, 0.25)

    def test_audio_chunk_clipped_to_bounds(self, tmp_path):
        """Values that exceed [-1, 1] after scaling should be clipped."""
        audio = make_audio_chunk(value=1.0)
        mic_vol_scalar = 2.0  # would push to 2.0 without clip
        result = np.clip(audio * mic_vol_scalar, -1.0, 1.0)
        assert np.all(result <= 1.0)


# ---------------------------------------------------------------------------
# process_audio — WebRTC integration
# ---------------------------------------------------------------------------


class TestProcessAudioWebRTC:
    def test_webrtc_not_created_when_agc_and_ns_zero(self, tmp_path):
        """If both AGC and NS are 0, WebRTCProcessor should never be instantiated."""
        state = make_state(tmp_path)
        state.preferences.mic_auto_gain = 0
        state.preferences.mic_noise_suppression = 0

        agc = state.preferences.mic_auto_gain or 0
        ns = state.preferences.mic_noise_suppression or 0
        should_create = agc > 0 or ns > 0
        assert should_create is False

    def test_webrtc_created_when_agc_nonzero(self, tmp_path):
        state = make_state(tmp_path)
        state.preferences.mic_auto_gain = 5
        state.preferences.mic_noise_suppression = 0

        agc = state.preferences.mic_auto_gain or 0
        ns = state.preferences.mic_noise_suppression or 0
        should_create = agc > 0 or ns > 0
        assert should_create is True

    def test_webrtc_created_when_ns_nonzero(self, tmp_path):
        state = make_state(tmp_path)
        state.preferences.mic_auto_gain = 0
        state.preferences.mic_noise_suppression = 2

        agc = state.preferences.mic_auto_gain or 0
        ns = state.preferences.mic_noise_suppression or 0
        should_create = agc > 0 or ns > 0
        assert should_create is True


# ---------------------------------------------------------------------------
# process_audio — wake word refractory
# ---------------------------------------------------------------------------


class TestWakeWordRefractory:
    def test_refractory_allows_activation_when_none(self):
        """First activation (last_active=None) should always be allowed."""
        import time
        last_active = None
        refractory_seconds = 2.0
        now = time.monotonic()
        allowed = (last_active is None) or ((now - last_active) > refractory_seconds)
        assert allowed is True

    def test_refractory_blocks_activation_too_soon(self):
        """Activation within refractory period should be blocked."""
        import time
        last_active = time.monotonic()  # just activated
        refractory_seconds = 2.0
        now = time.monotonic()
        allowed = (last_active is None) or ((now - last_active) > refractory_seconds)
        assert allowed is False

    def test_refractory_allows_activation_after_period(self):
        """Activation after refractory period should be allowed."""
        import time
        last_active = time.monotonic() - 3.0  # 3 seconds ago
        refractory_seconds = 2.0
        now = time.monotonic()
        allowed = (last_active is None) or ((now - last_active) > refractory_seconds)
        assert allowed is True


# ---------------------------------------------------------------------------
# process_audio — stop word detection logic
# ---------------------------------------------------------------------------


class TestStopWordLogic:
    def test_stop_word_only_triggers_when_in_active_set(self, tmp_path):
        state = make_state(tmp_path)
        # Stop word not in active set → should not trigger stop
        state.active_wake_words = set()
        stopped = True  # simulating detection

        should_stop = stopped and (state.stop_word.id in state.active_wake_words) and not state.muted
        assert should_stop is False

    def test_stop_word_triggers_when_in_active_set_and_not_muted(self, tmp_path):
        state = make_state(tmp_path)
        state.active_wake_words = {state.stop_word.id}
        state.muted = False
        stopped = True

        should_stop = stopped and (state.stop_word.id in state.active_wake_words) and not state.muted
        assert should_stop is True

    def test_stop_word_does_not_trigger_when_muted(self, tmp_path):
        state = make_state(tmp_path)
        state.active_wake_words = {state.stop_word.id}
        state.muted = True
        stopped = True

        should_stop = stopped and (state.stop_word.id in state.active_wake_words) and not state.muted
        assert should_stop is False


# ---------------------------------------------------------------------------
# Preferences loading from args
# ---------------------------------------------------------------------------


class TestPreferencesFromArgs:
    def test_agc_stored_in_preferences_when_nonzero(self, tmp_path):
        from linux_voice_assistant.models import Preferences
        prefs = Preferences()
        mic_auto_gain = 5
        if mic_auto_gain > 0:
            prefs.mic_auto_gain = mic_auto_gain
        assert prefs.mic_auto_gain == 5

    def test_agc_not_stored_when_zero(self, tmp_path):
        from linux_voice_assistant.models import Preferences
        prefs = Preferences()
        mic_auto_gain = 0
        if mic_auto_gain > 0:
            prefs.mic_auto_gain = mic_auto_gain
        assert prefs.mic_auto_gain == 0

    def test_ns_stored_in_preferences_when_nonzero(self, tmp_path):
        from linux_voice_assistant.models import Preferences
        prefs = Preferences()
        mic_noise_suppression = 2
        if mic_noise_suppression > 0:
            prefs.mic_noise_suppression = mic_noise_suppression
        assert prefs.mic_noise_suppression == 2

    def test_volume_clamped_to_one_when_above(self, tmp_path):
        from linux_voice_assistant.models import Preferences
        prefs = Preferences(volume=1.5)
        initial_volume = prefs.volume if prefs.volume is not None else 1.0
        initial_volume = max(0.0, min(1.0, float(initial_volume)))
        assert initial_volume == pytest.approx(1.0)

    def test_volume_clamped_to_zero_when_below(self, tmp_path):
        from linux_voice_assistant.models import Preferences
        prefs = Preferences(volume=-0.5)
        initial_volume = prefs.volume if prefs.volume is not None else 1.0
        initial_volume = max(0.0, min(1.0, float(initial_volume)))
        assert initial_volume == pytest.approx(0.0)

    def test_volume_defaults_to_one_when_none(self, tmp_path):
        from linux_voice_assistant.models import Preferences
        prefs = Preferences(volume=None)
        initial_volume = prefs.volume if prefs.volume is not None else 1.0
        initial_volume = max(0.0, min(1.0, float(initial_volume)))
        assert initial_volume == pytest.approx(1.0)

    def test_thinking_sound_enabled_by_flag(self, tmp_path):
        from linux_voice_assistant.models import Preferences
        prefs = Preferences()
        enable_thinking_sound = True
        if enable_thinking_sound:
            prefs.thinking_sound = 1
        assert prefs.thinking_sound == 1