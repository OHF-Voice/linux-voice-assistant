"""Sound feedback when the microphone re-opens for a follow-up question."""

from types import SimpleNamespace
from unittest.mock import MagicMock

from linux_voice_assistant.satellite import VoiceSatelliteProtocol


def _satellite(continue_conversation_sound: str, listen_during_wake_sound: bool = False) -> VoiceSatelliteProtocol:
    satellite = VoiceSatelliteProtocol.__new__(VoiceSatelliteProtocol)
    satellite.state = SimpleNamespace(  # type: ignore[assignment]
        continue_conversation_sound=continue_conversation_sound,
        continue_conversation_sound_enabled=True,
        continue_conversation_delay=0.0,
        listen_during_wake_sound=listen_during_wake_sound,
        muted=False,
        tts_player=MagicMock(),
        music_player=MagicMock(),
        active_wake_words=set(),
        stop_word=SimpleNamespace(id="stop"),
    )
    satellite.send_messages = MagicMock()  # type: ignore[method-assign]
    satellite._emit = MagicMock()  # type: ignore[method-assign]
    satellite._continue_conversation = True
    satellite._is_streaming_audio = False
    satellite._pipeline_active = True
    return satellite


def _run_settle_timer(satellite: VoiceSatelliteProtocol, timer_cls: MagicMock) -> None:
    """Invoke the callback that _tts_finished handed to threading.Timer."""
    delay, callback = timer_cls.call_args.args
    assert delay == satellite.state.continue_conversation_delay
    callback()


def test_plays_sound_before_opening_mic(monkeypatch):
    satellite = _satellite("wake.flac")
    timer_cls = MagicMock()
    monkeypatch.setattr("linux_voice_assistant.satellite.threading.Timer", timer_cls)

    satellite._tts_finished()
    _run_settle_timer(satellite, timer_cls)

    # Mic must stay closed until the chime finishes, otherwise it streams the chime.
    play_call = satellite.state.tts_player.play.call_args
    assert play_call.args[0] == "wake.flac"
    assert not satellite._is_streaming_audio

    play_call.kwargs["done_callback"]()
    assert satellite._is_streaming_audio


def test_listen_during_wake_sound_opens_mic_immediately(monkeypatch):
    satellite = _satellite("wake.flac", listen_during_wake_sound=True)
    timer_cls = MagicMock()
    monkeypatch.setattr("linux_voice_assistant.satellite.threading.Timer", timer_cls)

    satellite._tts_finished()
    _run_settle_timer(satellite, timer_cls)

    satellite.state.tts_player.play.assert_called_once_with("wake.flac")
    assert satellite._is_streaming_audio


def test_switch_off_disables_playback(monkeypatch):
    satellite = _satellite("wake.flac")
    satellite.state.continue_conversation_sound_enabled = False
    timer_cls = MagicMock()
    monkeypatch.setattr("linux_voice_assistant.satellite.threading.Timer", timer_cls)

    satellite._tts_finished()
    _run_settle_timer(satellite, timer_cls)

    satellite.state.tts_player.play.assert_not_called()
    assert satellite._is_streaming_audio


def test_muted_skips_sound_and_mic(monkeypatch):
    satellite = _satellite("wake.flac")
    satellite.state.muted = True
    timer_cls = MagicMock()
    monkeypatch.setattr("linux_voice_assistant.satellite.threading.Timer", timer_cls)

    satellite._tts_finished()
    _run_settle_timer(satellite, timer_cls)

    satellite.state.tts_player.play.assert_not_called()
    assert not satellite._is_streaming_audio


def test_no_sound_when_conversation_ends(monkeypatch):
    satellite = _satellite("wake.flac")
    satellite._continue_conversation = False
    timer_cls = MagicMock()
    monkeypatch.setattr("linux_voice_assistant.satellite.threading.Timer", timer_cls)

    satellite._tts_finished()

    timer_cls.assert_not_called()
    satellite.state.tts_player.play.assert_not_called()
    satellite.state.music_player.unduck.assert_called_once()


def test_switch_persists_to_preferences():
    satellite = _satellite("wake.flac")
    satellite.state.continue_conversation_sound_enabled = False
    satellite.state.preferences = SimpleNamespace(continue_conversation_sound=0)
    satellite.state.save_preferences = MagicMock()

    satellite._set_continue_conversation_sound_enabled(True)

    assert satellite.state.continue_conversation_sound_enabled
    assert satellite.state.preferences.continue_conversation_sound == 1
    satellite.state.save_preferences.assert_called_once()
