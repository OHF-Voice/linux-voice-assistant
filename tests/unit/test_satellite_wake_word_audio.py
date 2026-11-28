"""Unit tests for wake word verification in the satellite: subscription, start request, accept, reject and timeout."""

import logging
from typing import Any, Callable, List, Optional
from unittest.mock import MagicMock, patch

from aioesphomeapi.api_pb2 import (  # type: ignore[attr-defined]  # pylint: disable=no-name-in-module
    SubscribeVoiceAssistantRequest,
    SwitchCommandRequest,
    VoiceAssistantAudio,
    VoiceAssistantRequest,
)
from aioesphomeapi.model import VoiceAssistantEventType, VoiceAssistantFeature, VoiceAssistantSubscriptionFlag

from linux_voice_assistant.models import DetectionResult
from linux_voice_assistant.peripheral_api import LVAEvent
from linux_voice_assistant.wake_word_audio import AudioChunk
from tests.unit.conftest import make_satellite

WAKE_WORD_AUDIO_FLAGS = VoiceAssistantSubscriptionFlag.API_AUDIO | VoiceAssistantSubscriptionFlag.WAKE_WORD_AUDIO
DETECTION = DetectionResult("Internal - MWW - Hey Jarvis", 0, 0.93, 0.7)
BUFFER = (AudioChunk(b"\x01\x00" * 1024, b"\x02\x00" * 1024),)


class FakeTimer:
    """A threading.Timer that only runs when the test says so."""

    created: List["FakeTimer"] = []

    def __init__(self, interval: float, function: Callable[[], Any]) -> None:
        self.interval = interval
        self.function = function
        self.cancelled = False
        self.daemon = False
        FakeTimer.created.append(self)

    def start(self) -> None:
        pass

    def cancel(self) -> None:
        self.cancelled = True

    def fire(self) -> None:
        self.function()


def verifying_satellite(tmp_path, flags: Optional[int] = WAKE_WORD_AUDIO_FLAGS, enabled: bool = True):
    sat = make_satellite(tmp_path)
    sat.state.wake_word_verification_enabled = enabled
    sat.send_messages = MagicMock()
    sat._emit = MagicMock()
    sat.duck = MagicMock()
    list(sat.handle_message(SubscribeVoiceAssistantRequest(subscribe=flags is not None, flags=flags or 0)))
    return sat


def sent(sat) -> List[Any]:
    return [message for call in sat.send_messages.call_args_list for message in call.args[0]]


def emitted(sat) -> List[LVAEvent]:
    return [call.args[0] for call in sat._emit.call_args_list]


def start_verified_run(sat):
    FakeTimer.created = []
    with patch("linux_voice_assistant.satellite.threading.Timer", FakeTimer):
        sat.wakeup(MagicMock(wake_word="Hey Jarvis"), DETECTION, BUFFER)


class TestFeatureFlag:
    def test_wake_word_audio_is_reported(self, tmp_path):
        sat = make_satellite(tmp_path)
        assert sat.supported_features & VoiceAssistantFeature.WAKE_WORD_AUDIO

    def test_not_reported_output_only(self, tmp_path):
        sat = make_satellite(tmp_path, state_overrides={"output_only": True})
        assert not sat.supported_features & VoiceAssistantFeature.WAKE_WORD_AUDIO


class TestSubscription:
    def test_flag_activates_the_mode(self, tmp_path):
        assert verifying_satellite(tmp_path).wake_word_audio_active

    def test_without_flag_the_mode_is_off(self, tmp_path):
        assert not verifying_satellite(tmp_path, flags=VoiceAssistantSubscriptionFlag.API_AUDIO).wake_word_audio_active

    def test_not_subscribed_the_mode_is_off(self, tmp_path):
        assert not make_satellite(tmp_path).wake_word_audio_active

    def test_unsubscribe_ends_the_mode(self, tmp_path):
        sat = verifying_satellite(tmp_path)

        list(sat.handle_message(SubscribeVoiceAssistantRequest(subscribe=False)))

        assert not sat.wake_word_audio_active

    def test_subscribing_again_without_flag_ends_the_mode(self, tmp_path):
        sat = verifying_satellite(tmp_path)

        list(sat.handle_message(SubscribeVoiceAssistantRequest(subscribe=True, flags=VoiceAssistantSubscriptionFlag.API_AUDIO)))

        assert not sat.wake_word_audio_active

    def test_switch_off_ends_the_mode(self, tmp_path):
        assert not verifying_satellite(tmp_path, enabled=False).wake_word_audio_active

    def test_disconnect_ends_the_mode(self, tmp_path):
        sat = verifying_satellite(tmp_path)

        sat.connection_lost(None)

        assert not sat.wake_word_audio_active

    def test_subscription_is_logged(self, tmp_path, caplog):
        with caplog.at_level(logging.DEBUG):
            verifying_satellite(tmp_path)

        assert "Home Assistant subscribed to the voice assistant (flags 12, wake word audio requested)" in caplog.text


class TestVerifiedStart:
    def test_start_request_carries_the_wake_word_audio(self, tmp_path):
        sat = verifying_satellite(tmp_path)

        start_verified_run(sat)

        request = sent(sat)[0]
        assert isinstance(request, VoiceAssistantRequest)
        assert request.start
        assert request.wake_word_id == DETECTION.wake_word_id
        assert request.wake_word_audio_bytes == 2048
        assert request.wake_word_audio_bytes2 == 2048

    def test_buffered_audio_follows_the_request(self, tmp_path):
        sat = verifying_satellite(tmp_path)

        start_verified_run(sat)

        audio = [message for message in sent(sat) if isinstance(message, VoiceAssistantAudio)]
        assert b"".join(m.data for m in audio) == BUFFER[0].data
        assert b"".join(m.data2 for m in audio) == BUFFER[0].data2

    def test_live_audio_streams_at_once(self, tmp_path):
        sat = verifying_satellite(tmp_path)

        start_verified_run(sat)

        assert sat._is_streaming_audio
        assert emitted(sat) == [LVAEvent.WAKE_WORD_DETECTED, LVAEvent.LISTENING]

    def test_no_wake_sound_and_no_ducking(self, tmp_path):
        sat = verifying_satellite(tmp_path)

        start_verified_run(sat)

        sat.state.tts_player.play.assert_not_called()
        sat.duck.assert_not_called()

    def test_verdict_timeout_starts(self, tmp_path):
        sat = verifying_satellite(tmp_path)

        start_verified_run(sat)

        assert [timer.interval for timer in FakeTimer.created] == [2.0]

    def test_switch_off_starts_as_before(self, tmp_path):
        sat = verifying_satellite(tmp_path, enabled=False)

        start_verified_run(sat)

        assert not any(isinstance(m, VoiceAssistantRequest) for m in sent(sat))
        sat.state.tts_player.play.assert_called_once()
        sat.duck.assert_called_once()

    def test_without_buffered_audio_starts_as_before(self, tmp_path):
        sat = verifying_satellite(tmp_path)

        sat.wakeup(MagicMock(wake_word="Hey Jarvis"), DETECTION)

        sat.state.tts_player.play.assert_called_once()

    def test_start_is_logged(self, tmp_path, caplog):
        sat = verifying_satellite(tmp_path)

        with caplog.at_level(logging.DEBUG):
            start_verified_run(sat)

        assert "with wake word audio for verification (phrase 'Internal - MWW - Hey Jarvis', probability 0.930, threshold 0.700)" in caplog.text


class TestVerdict:
    def test_accepted_ducks_and_stops_the_timeout(self, tmp_path):
        sat = verifying_satellite(tmp_path)
        start_verified_run(sat)

        sat.handle_voice_event(VoiceAssistantEventType.VOICE_ASSISTANT_WAKE_WORD_END, {})

        sat.duck.assert_called_once_with()
        assert FakeTimer.created[0].cancelled

    def test_wake_word_end_without_verification_does_not_duck(self, tmp_path):
        sat = verifying_satellite(tmp_path)

        sat.handle_voice_event(VoiceAssistantEventType.VOICE_ASSISTANT_WAKE_WORD_END, {})

        sat.duck.assert_not_called()

    def test_rejected_goes_idle(self, tmp_path):
        sat = verifying_satellite(tmp_path)
        start_verified_run(sat)
        sat._emit.reset_mock()

        sat.handle_voice_event(VoiceAssistantEventType.VOICE_ASSISTANT_WAKE_WORD_REJECTED, {})  # pylint: disable=no-member

        assert not sat._is_streaming_audio
        assert not sat.pipeline_active
        assert emitted(sat) == [LVAEvent.WAKE_WORD_REJECTED, LVAEvent.IDLE]
        assert FakeTimer.created[0].cancelled
        sat.duck.assert_not_called()

    def test_run_end_after_rejection_is_quiet(self, tmp_path):
        sat = verifying_satellite(tmp_path)
        start_verified_run(sat)
        sat.handle_voice_event(VoiceAssistantEventType.VOICE_ASSISTANT_WAKE_WORD_REJECTED, {})  # pylint: disable=no-member
        sat._emit.reset_mock()
        sat.send_messages.reset_mock()

        sat.handle_voice_event(VoiceAssistantEventType.VOICE_ASSISTANT_RUN_END, {})

        sat.state.tts_player.play.assert_not_called()
        sat._emit.assert_not_called()
        sat.send_messages.assert_not_called()

    def test_next_run_ends_normally_again(self, tmp_path):
        sat = verifying_satellite(tmp_path)
        start_verified_run(sat)
        sat.handle_voice_event(VoiceAssistantEventType.VOICE_ASSISTANT_WAKE_WORD_REJECTED, {})  # pylint: disable=no-member
        sat._emit.reset_mock()

        sat.handle_voice_event(VoiceAssistantEventType.VOICE_ASSISTANT_RUN_START, {})
        sat.handle_voice_event(VoiceAssistantEventType.VOICE_ASSISTANT_RUN_END, {})

        sat.state.tts_player.play.assert_called_once_with(sat.state.pipeline_ended_sound, done_callback=sat._pipeline_ended_sound_finished)

    def test_rejection_is_logged(self, tmp_path, caplog):
        sat = verifying_satellite(tmp_path)
        start_verified_run(sat)

        with caplog.at_level(logging.DEBUG):
            sat.handle_voice_event(VoiceAssistantEventType.VOICE_ASSISTANT_WAKE_WORD_REJECTED, {})  # pylint: disable=no-member

        assert "Wake word verification ended: rejected" in caplog.text


class TestTimeout:
    @staticmethod
    def fire_timeout() -> None:
        with patch("linux_voice_assistant.satellite.threading.Timer", FakeTimer):
            FakeTimer.created[0].fire()

    def test_stops_the_run_and_shows_the_error(self, tmp_path):
        sat = verifying_satellite(tmp_path)
        start_verified_run(sat)
        sat._emit.reset_mock()
        sat.send_messages.reset_mock()

        self.fire_timeout()

        assert not sat._is_streaming_audio
        assert sent(sat) == [VoiceAssistantRequest(start=False)]
        assert emitted(sat) == [LVAEvent.PIPELINE_ERROR]
        assert sat.pipeline_active

    def test_goes_idle_after_one_second(self, tmp_path):
        sat = verifying_satellite(tmp_path)
        start_verified_run(sat)
        self.fire_timeout()
        sat._emit.reset_mock()

        idle_timer = FakeTimer.created[1]
        idle_timer.fire()

        assert idle_timer.interval == 1.0
        assert not sat.pipeline_active
        assert emitted(sat) == [LVAEvent.IDLE]

    def test_events_after_the_timeout_change_nothing(self, tmp_path):
        sat = verifying_satellite(tmp_path)
        start_verified_run(sat)
        self.fire_timeout()
        sat._emit.reset_mock()

        sat.handle_voice_event(VoiceAssistantEventType.VOICE_ASSISTANT_ERROR, {"code": "stt-stream-failed"})
        sat.handle_voice_event(VoiceAssistantEventType.VOICE_ASSISTANT_RUN_END, {})

        sat._emit.assert_not_called()
        sat.state.tts_player.play.assert_not_called()

    def test_late_rejection_is_ignored(self, tmp_path, caplog):
        sat = verifying_satellite(tmp_path)
        start_verified_run(sat)
        self.fire_timeout()
        sat._emit.reset_mock()

        with caplog.at_level(logging.DEBUG):
            sat.handle_voice_event(VoiceAssistantEventType.VOICE_ASSISTANT_WAKE_WORD_REJECTED, {})  # pylint: disable=no-member

        sat._emit.assert_not_called()
        assert sat.pipeline_active
        assert "Ignoring wake word rejection, LVA is not waiting for a verdict" in caplog.text

    def test_late_verdict_is_ignored(self, tmp_path):
        sat = verifying_satellite(tmp_path)
        start_verified_run(sat)
        self.fire_timeout()

        sat.handle_voice_event(VoiceAssistantEventType.VOICE_ASSISTANT_WAKE_WORD_END, {})

        sat.duck.assert_not_called()

    def test_cancelled_timeout_does_nothing(self, tmp_path):
        sat = verifying_satellite(tmp_path)
        start_verified_run(sat)
        sat.handle_voice_event(VoiceAssistantEventType.VOICE_ASSISTANT_WAKE_WORD_END, {})
        sat.send_messages.reset_mock()

        FakeTimer.created[0].fire()

        sat.send_messages.assert_not_called()

    def test_timeout_is_logged(self, tmp_path, caplog):
        sat = verifying_satellite(tmp_path)
        start_verified_run(sat)

        with caplog.at_level(logging.WARNING):
            self.fire_timeout()

        assert "neither accepted nor rejected the wake word within 2.0 s (wake-word-verification-timeout)" in caplog.text


class TestTimeoutCancelled:
    def test_by_run_end(self, tmp_path):
        sat = verifying_satellite(tmp_path)
        start_verified_run(sat)

        sat.handle_voice_event(VoiceAssistantEventType.VOICE_ASSISTANT_RUN_END, {})

        assert FakeTimer.created[0].cancelled

    def test_by_error(self, tmp_path):
        sat = verifying_satellite(tmp_path)
        start_verified_run(sat)

        sat.handle_voice_event(VoiceAssistantEventType.VOICE_ASSISTANT_ERROR, {"code": "stt-no-text-recognized"})

        assert FakeTimer.created[0].cancelled
        assert LVAEvent.PIPELINE_ERROR in emitted(sat)

    def test_by_stop(self, tmp_path):
        sat = verifying_satellite(tmp_path)
        start_verified_run(sat)

        sat.stop()

        assert FakeTimer.created[0].cancelled

    def test_by_mute(self, tmp_path):
        sat = verifying_satellite(tmp_path)
        start_verified_run(sat)

        sat._set_muted(True)

        assert FakeTimer.created[0].cancelled

    def test_by_disconnect(self, tmp_path):
        sat = verifying_satellite(tmp_path)
        start_verified_run(sat)

        sat.connection_lost(None)

        assert FakeTimer.created[0].cancelled


class TestVerificationSwitch:
    def test_switch_is_created(self, tmp_path):
        sat = make_satellite(tmp_path)

        assert sat.state.wake_word_verification_entity in sat.state.entities

    def test_turning_off_is_stored(self, tmp_path):
        sat = make_satellite(tmp_path)
        entity = sat.state.wake_word_verification_entity

        list(entity.handle_message(SwitchCommandRequest(key=entity.key, state=False)))

        assert not sat.state.wake_word_verification_enabled
        assert sat.state.preferences.wake_word_verification == 0
        assert sat.state.preferences_path.exists()

    def test_reconnect_reuses_the_switch(self, tmp_path):
        sat = make_satellite(tmp_path)
        entity = sat.state.wake_word_verification_entity

        from linux_voice_assistant.satellite import VoiceSatelliteProtocol

        with (
            patch("linux_voice_assistant.satellite.WakeWord1SensitivityNumberEntity", MagicMock()),
            patch("linux_voice_assistant.satellite.WakeWord2SensitivityNumberEntity", MagicMock()),
            patch("linux_voice_assistant.satellite.StopWordSensitivityNumberEntity", MagicMock()),
        ):
            second = VoiceSatelliteProtocol(sat.state)

        assert second.state.wake_word_verification_entity is entity
        assert sat.state.entities.count(entity) == 1
        assert entity.server is second
