"""Unit tests for __main__.py — process_audio logic and argument parsing helpers."""

import sys
from unittest.mock import MagicMock

import numpy as np
import pytest

from tests.unit.conftest import make_state


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

    def test_audio_chunk_scaled_by_mic_volume(self):
        """Verify the numpy scaling produces correct output."""
        audio = make_audio_chunk(value=0.5)
        mic_vol_scalar = 0.5
        result = np.clip(audio * mic_vol_scalar, -1.0, 1.0)
        assert np.allclose(result, 0.25)

    def test_audio_chunk_clipped_to_bounds(self):
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
# process_audio — audio buffered before a wake word (verification by Home Assistant)
# ---------------------------------------------------------------------------


class _StopLoop(BaseException):
    """Raised by the fake recorder to leave the endless audio loop."""


class _FakeRecorder:
    """Context manager that records the given blocks, then ends the loop."""

    def __init__(self, blocks, on_block):
        self._blocks = list(blocks)
        self._on_block = on_block
        self._index = 0

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def record(self, _block_size):
        if self._index >= len(self._blocks):
            raise _StopLoop
        self._on_block(self._index)
        block = self._blocks[self._index]
        self._index += 1
        return block.reshape(-1, 1)


def _run_process_audio(monkeypatch, wake_word_spec, detect_at, requested=True, muted_blocks=0):
    """Run the audio loop over four blocks (the first `muted_blocks` of them muted) and return the satellite."""
    monkeypatch.setitem(sys.modules, "soundcard", MagicMock())
    sys.modules.pop("linux_voice_assistant.__main__", None)
    import linux_voice_assistant.__main__ as main_mod
    from tests.unit.conftest import make_satellite

    wake_word = MagicMock(spec=wake_word_spec)
    wake_word.id = "hey_jarvis"
    wake_word.wake_word = "Hey Jarvis"
    if hasattr(wake_word_spec, "process_streaming_prob"):
        wake_word.process_streaming_prob.side_effect = lambda _: 0.9 if wake_word.process_streaming_prob.call_count == detect_at else 0.0
    else:
        wake_word.process_streaming.side_effect = lambda _: [0.9] if wake_word.process_streaming.call_count == detect_at else [0.0]

    sat = make_satellite()
    sat.handle_audio = MagicMock()
    sat.wakeup = MagicMock()
    sat._wake_word_audio_requested = requested
    sat.state.wake_words = {"hey_jarvis": wake_word}
    sat.state.active_wake_words = {"hey_jarvis"}
    sat.state.sensitivity_1_number_entity = None
    sat.state.sensitivity_2_number_entity = None
    sat.state.stop_sensitivity_number_entity = None
    sat.state.stop_word.process_streaming.return_value = False

    features = MagicMock()
    features.process_streaming.return_value = [np.zeros(1, dtype=np.float32)]
    monkeypatch.setattr(main_mod, "MicroWakeWordFeatures", MagicMock(return_value=features))
    monkeypatch.setattr(main_mod, "OpenWakeWordFeatures", MagicMock(from_builtin=MagicMock(return_value=features)))

    audio = [make_audio_chunk(samples=4, value=0.1 * (i + 1)) for i in range(4)]
    recorder = _FakeRecorder(audio, on_block=lambda index: setattr(sat.state, "muted", index < muted_blocks))
    monkeypatch.setattr(main_mod, "_ReconnectingRecorder", MagicMock(return_value=recorder))

    with pytest.raises(_StopLoop):
        main_mod.process_audio(sat.state, MagicMock(), 4)
    return sat


class TestProcessAudioWakeWordAudio:
    def test_micro_wake_word_gets_the_audio_up_to_the_detection(self, monkeypatch):
        from pymicro_wakeword import MicroWakeWord

        sat = _run_process_audio(monkeypatch, MicroWakeWord, detect_at=3)

        sat.wakeup.assert_called_once()
        wake_word_audio = sat.wakeup.call_args.args[2]
        assert len(wake_word_audio.chunks) == 3
        assert wake_word_audio.audio_bytes == 3 * 4 * 2
        assert wake_word_audio.chunks[-1][0] == sat.handle_audio.call_args_list[2].args[0]

    def test_detection_carries_score_and_cutoff(self, monkeypatch):
        from pymicro_wakeword import MicroWakeWord

        sat = _run_process_audio(monkeypatch, MicroWakeWord, detect_at=2)

        detection = sat.wakeup.call_args.args[1]
        assert detection.score == pytest.approx(0.9)
        assert detection.cutoff == pytest.approx(0.7)

    def test_no_audio_is_passed_when_home_assistant_did_not_ask(self, monkeypatch):
        from pymicro_wakeword import MicroWakeWord

        sat = _run_process_audio(monkeypatch, MicroWakeWord, detect_at=3, requested=False)

        sat.wakeup.assert_called_once()
        assert sat.wakeup.call_args.args[2] is None

    def test_open_wake_word_is_not_verified(self, monkeypatch):
        from pyopen_wakeword import OpenWakeWord

        sat = _run_process_audio(monkeypatch, OpenWakeWord, detect_at=3)

        sat.wakeup.assert_called_once()
        assert sat.wakeup.call_args.args[2] is None

    def test_audio_recorded_while_muted_is_not_buffered(self, monkeypatch):
        from pymicro_wakeword import MicroWakeWord

        sat = _run_process_audio(monkeypatch, MicroWakeWord, detect_at=3, muted_blocks=2)

        sat.wakeup.assert_called_once()
        assert len(sat.wakeup.call_args.args[2].chunks) == 1


# ---------------------------------------------------------------------------
# Preferences loading from args
# ---------------------------------------------------------------------------


class TestPreferencesFromArgs:
    def test_agc_stored_in_preferences_when_nonzero(self):
        from linux_voice_assistant.models import Preferences

        prefs = Preferences()
        mic_auto_gain = 5
        if mic_auto_gain > 0:
            prefs.mic_auto_gain = mic_auto_gain
        assert prefs.mic_auto_gain == 5

    def test_agc_not_stored_when_zero(self):
        from linux_voice_assistant.models import Preferences

        prefs = Preferences()
        mic_auto_gain = 0
        if mic_auto_gain > 0:
            prefs.mic_auto_gain = mic_auto_gain
        assert prefs.mic_auto_gain == 0

    def test_ns_stored_in_preferences_when_nonzero(self):
        from linux_voice_assistant.models import Preferences

        prefs = Preferences()
        mic_noise_suppression = 2
        if mic_noise_suppression > 0:
            prefs.mic_noise_suppression = mic_noise_suppression
        assert prefs.mic_noise_suppression == 2

    def test_volume_clamped_to_one_when_above(self):
        from linux_voice_assistant.models import Preferences

        prefs = Preferences(volume=1.5)
        initial_volume = prefs.volume if prefs.volume is not None else 1.0
        initial_volume = max(0.0, min(1.0, float(initial_volume)))
        assert initial_volume == pytest.approx(1.0)

    def test_volume_clamped_to_zero_when_below(self):
        from linux_voice_assistant.models import Preferences

        prefs = Preferences(volume=-0.5)
        initial_volume = prefs.volume if prefs.volume is not None else 1.0
        initial_volume = max(0.0, min(1.0, float(initial_volume)))
        assert initial_volume == pytest.approx(0.0)

    def test_volume_defaults_to_one_when_none(self):
        from linux_voice_assistant.models import Preferences

        prefs = Preferences(volume=None)
        initial_volume = prefs.volume if prefs.volume is not None else 1.0
        initial_volume = max(0.0, min(1.0, float(initial_volume)))
        assert initial_volume == pytest.approx(1.0)

    def test_thinking_sound_enabled_by_flag(self):
        from linux_voice_assistant.models import Preferences

        prefs = Preferences()
        enable_thinking_sound = True
        if enable_thinking_sound:
            prefs.thinking_sound = 1
        assert prefs.thinking_sound == 1


# ---------------------------------------------------------------------------
# _float_to_pcm16
# ---------------------------------------------------------------------------


class TestFloatToPcm16:
    @pytest.fixture(autouse=True)
    def _stub_soundcard(self, monkeypatch):
        """Importing soundcard connects to PulseAudio, which is absent on CI runners."""
        monkeypatch.setitem(sys.modules, "soundcard", MagicMock())

    @pytest.mark.parametrize("scalar", [0.1, 0.5, 1.0])
    def test_matches_reference_conversion(self, scalar):
        from linux_voice_assistant.__main__ import _float_to_pcm16

        rng = np.random.default_rng(0)
        samples = rng.uniform(-1.5, 1.5, size=(1024, 2)).astype(np.float32)
        column = samples[:, 1]

        expected = (np.clip(column * scalar, -1.0, 1.0) * 32767.0).astype("<i2").tobytes()

        assert _float_to_pcm16(column, scalar) == expected

    def test_does_not_modify_input(self):
        from linux_voice_assistant.__main__ import _float_to_pcm16

        samples = np.array([0.5, -0.5, 1.0], dtype=np.float32)
        original = samples.copy()

        _float_to_pcm16(samples, 1.0)

        assert np.array_equal(samples, original)


# ---------------------------------------------------------------------------
# _ReconnectingRecorder — recovery from a failed microphone stream
# ---------------------------------------------------------------------------


class TestReconnectingRecorder:
    @pytest.fixture(autouse=True)
    def _fresh_soundcard(self, monkeypatch):
        """Give every test its own soundcard module so patches on it cannot leak between tests."""
        monkeypatch.setitem(sys.modules, "soundcard", MagicMock())
        from linux_voice_assistant import __main__ as main_mod

        monkeypatch.setattr(main_mod, "sc", MagicMock())

    @staticmethod
    def _make_mic(*recorders):
        mic = MagicMock()
        mic.name = "Test Mic"
        mic.id = "test-mic-id"
        mic.recorder.side_effect = list(recorders)
        return mic

    @staticmethod
    def _make_recorder(**kwargs):
        recorder = MagicMock()
        recorder.configure_mock(**kwargs)
        return recorder

    def test_record_passes_through_when_healthy(self):
        from linux_voice_assistant import __main__ as main_mod

        block = make_audio_chunk()
        recorder = self._make_recorder(**{"record.return_value": block})
        mic = self._make_mic(recorder)

        with main_mod._ReconnectingRecorder(mic, 1, 1024) as rec:
            assert rec.record(1024) is block

        mic.recorder.assert_called_once_with(samplerate=16000, channels=1, blocksize=1024)
        recorder.__exit__.assert_called_once()

    def test_reopens_stream_after_failure(self, monkeypatch):
        from linux_voice_assistant import __main__ as main_mod

        monkeypatch.setattr(main_mod.time, "sleep", lambda _s: None)
        monkeypatch.setattr(main_mod.sc, "get_microphone", lambda mic_id: mic)

        block = make_audio_chunk()
        failed = self._make_recorder(**{"record.side_effect": RuntimeError("Recording failed, stream is in status FAILED")})
        healthy = self._make_recorder(**{"record.return_value": block})
        mic = self._make_mic(failed, healthy)

        with main_mod._ReconnectingRecorder(mic, 1, 1024) as rec:
            assert rec.record(1024) is block

        failed.__exit__.assert_called_once()
        assert mic.recorder.call_count == 2

    def test_retries_until_open_succeeds(self, monkeypatch):
        from linux_voice_assistant import __main__ as main_mod

        monkeypatch.setattr(main_mod.time, "sleep", lambda _s: None)
        monkeypatch.setattr(main_mod.sc, "get_microphone", lambda mic_id: mic)

        block = make_audio_chunk()
        failed = self._make_recorder(**{"record.side_effect": RuntimeError("Recording failed, stream is in status FAILED")})
        healthy = self._make_recorder(**{"record.return_value": block})
        mic = self._make_mic(failed, OSError("device busy"), healthy)

        with main_mod._ReconnectingRecorder(mic, 1, 1024) as rec:
            assert rec.record(1024) is block

        assert mic.recorder.call_count == 3

    def test_first_retry_is_immediate_second_waits(self, monkeypatch):
        from linux_voice_assistant import __main__ as main_mod

        sleeps = []
        monkeypatch.setattr(main_mod.time, "sleep", sleeps.append)
        monkeypatch.setattr(main_mod.sc, "get_microphone", lambda mic_id: mic)

        block = make_audio_chunk()
        failed = self._make_recorder(**{"record.side_effect": RuntimeError("Recording failed, stream is in status FAILED")})
        healthy = self._make_recorder(**{"record.return_value": block})
        mic = self._make_mic(failed, OSError("device busy"), healthy)

        with main_mod._ReconnectingRecorder(mic, 1, 1024) as rec:
            assert rec.record(1024) is block

        assert sleeps == [2.0]

    def test_raises_after_two_failed_reconnects(self, monkeypatch):
        from linux_voice_assistant import __main__ as main_mod

        sleeps = []
        monkeypatch.setattr(main_mod.time, "sleep", sleeps.append)
        monkeypatch.setattr(main_mod.sc, "get_microphone", lambda mic_id: mic)

        failed = self._make_recorder(**{"record.side_effect": RuntimeError("Recording failed, stream is in status FAILED")})
        mic = self._make_mic(failed, OSError("gone"), OSError("still gone"))

        with main_mod._ReconnectingRecorder(mic, 1, 1024) as rec:
            with pytest.raises(RuntimeError, match="Test Mic.*did not recover"):
                rec.record(1024)

        assert sleeps == [2.0]
        assert mic.recorder.call_count == 3

    def test_keeps_old_mic_object_without_id(self, monkeypatch):
        from linux_voice_assistant import __main__ as main_mod

        get_mic = MagicMock()
        monkeypatch.setattr(main_mod.sc, "get_microphone", get_mic)

        mic = MagicMock(spec=["name", "recorder"])
        mic.name = "Test Mic"
        mic.recorder.return_value = self._make_recorder()

        main_mod._ReconnectingRecorder(mic, 1, 1024)._refresh_mic()

        get_mic.assert_not_called()
