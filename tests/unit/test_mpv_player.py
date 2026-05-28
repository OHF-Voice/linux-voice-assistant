"""Unit tests for MpvMediaPlayer."""

import pytest
from unittest.mock import MagicMock, patch, call


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_player(device=None):
    """Return an MpvMediaPlayer with the internal LibMpvPlayer mocked out."""
    from linux_voice_assistant.player.state import PlayerState

    mock_lib_player = MagicMock()
    mock_lib_player.state.return_value = PlayerState.IDLE

    with patch("linux_voice_assistant.mpv_player.LibMpvPlayer", return_value=mock_lib_player):
        from linux_voice_assistant.mpv_player import MpvMediaPlayer
        player = MpvMediaPlayer(device=device)
        player._mock = mock_lib_player
        return player


# ---------------------------------------------------------------------------
# Initialization
# ---------------------------------------------------------------------------


class TestInit:
    def test_player_initialized_with_device(self):
        with patch("linux_voice_assistant.mpv_player.LibMpvPlayer") as mock_cls:
            mock_cls.return_value = MagicMock()
            from linux_voice_assistant.mpv_player import MpvMediaPlayer
            MpvMediaPlayer(device="hw:1,0")
            mock_cls.assert_called_once_with(device="hw:1,0")

    def test_player_initialized_with_none_device(self):
        with patch("linux_voice_assistant.mpv_player.LibMpvPlayer") as mock_cls:
            mock_cls.return_value = MagicMock()
            from linux_voice_assistant.mpv_player import MpvMediaPlayer
            MpvMediaPlayer(device=None)
            mock_cls.assert_called_once_with(device=None)

    def test_done_callback_starts_none(self):
        player = make_player()
        assert player._done_callback is None

    def test_playlist_starts_empty(self):
        player = make_player()
        assert player._playlist == []


# ---------------------------------------------------------------------------
# play()
# ---------------------------------------------------------------------------


class TestPlay:
    def test_play_single_url_calls_lib_player(self):
        player = make_player()
        player.play("http://example.com/audio.mp3")
        player._mock.play.assert_called_once()

    def test_play_single_url_passes_correct_url(self):
        player = make_player()
        player.play("http://example.com/audio.mp3")
        args, kwargs = player._mock.play.call_args
        assert args[0] == "http://example.com/audio.mp3"

    def test_play_list_plays_first_url(self):
        player = make_player()
        player.play(["http://a.com/1.mp3", "http://a.com/2.mp3"])
        args, kwargs = player._mock.play.call_args
        assert args[0] == "http://a.com/1.mp3"

    def test_play_list_stores_remaining_in_playlist(self):
        player = make_player()
        player.play(["http://a.com/1.mp3", "http://a.com/2.mp3", "http://a.com/3.mp3"])
        assert player._playlist == ["http://a.com/2.mp3", "http://a.com/3.mp3"]

    def test_play_single_url_playlist_is_empty(self):
        player = make_player()
        player.play("http://example.com/audio.mp3")
        assert player._playlist == []

    def test_play_stores_done_callback(self):
        player = make_player()
        cb = MagicMock()
        player.play("http://example.com/audio.mp3", done_callback=cb)
        assert player._done_callback is cb

    def test_play_empty_list_does_not_call_lib_player(self):
        player = make_player()
        player.play([])
        player._mock.play.assert_not_called()

    def test_play_while_active_stops_previous(self):
        from linux_voice_assistant.player.state import PlayerState
        player = make_player()
        player._done_callback = MagicMock()  # simulate active playback
        player._mock.state.return_value = PlayerState.PLAYING

        player.play("http://example.com/new.mp3")
        player._mock.stop.assert_called_once()

    def test_play_while_idle_does_not_stop(self):
        from linux_voice_assistant.player.state import PlayerState
        player = make_player()
        player._mock.state.return_value = PlayerState.IDLE
        player._done_callback = None

        player.play("http://example.com/new.mp3")
        player._mock.stop.assert_not_called()


# ---------------------------------------------------------------------------
# _on_track_finished()
# ---------------------------------------------------------------------------


class TestOnTrackFinished:
    def test_plays_next_url_when_playlist_has_items(self):
        player = make_player()
        player._playlist = ["http://a.com/2.mp3"]
        player._on_track_finished()
        args, _ = player._mock.play.call_args
        assert args[0] == "http://a.com/2.mp3"

    def test_invokes_done_callback_when_playlist_empty(self):
        player = make_player()
        cb = MagicMock()
        player._done_callback = cb
        player._playlist = []
        player._on_track_finished()
        cb.assert_called_once()

    def test_clears_done_callback_after_invoking(self):
        player = make_player()
        player._done_callback = MagicMock()
        player._playlist = []
        player._on_track_finished()
        assert player._done_callback is None

    def test_no_error_when_done_callback_is_none(self):
        player = make_player()
        player._done_callback = None
        player._playlist = []
        player._on_track_finished()  # should not raise


# ---------------------------------------------------------------------------
# pause() / resume() / stop()
# ---------------------------------------------------------------------------


class TestPauseResumeStop:
    def test_pause_delegates_to_lib_player(self):
        player = make_player()
        player.pause()
        player._mock.pause.assert_called_once()

    def test_resume_delegates_to_lib_player(self):
        player = make_player()
        player.resume()
        player._mock.resume.assert_called_once()

    def test_stop_delegates_to_lib_player(self):
        player = make_player()
        player.stop()
        player._mock.stop.assert_called_once()

    def test_stop_invokes_done_callback(self):
        player = make_player()
        cb = MagicMock()
        player._done_callback = cb
        player.stop()
        cb.assert_called_once()

    def test_stop_clears_done_callback(self):
        player = make_player()
        player._done_callback = MagicMock()
        player.stop()
        assert player._done_callback is None

    def test_stop_no_error_when_no_callback(self):
        player = make_player()
        player._done_callback = None
        player.stop()  # should not raise


# ---------------------------------------------------------------------------
# is_playing
# ---------------------------------------------------------------------------


class TestIsPlaying:
    def test_true_when_playing(self):
        from linux_voice_assistant.player.state import PlayerState
        player = make_player()
        player._mock.state.return_value = PlayerState.PLAYING
        assert player.is_playing is True

    def test_true_when_paused(self):
        from linux_voice_assistant.player.state import PlayerState
        player = make_player()
        player._mock.state.return_value = PlayerState.PAUSED
        assert player.is_playing is True

    def test_true_when_loading(self):
        from linux_voice_assistant.player.state import PlayerState
        player = make_player()
        player._mock.state.return_value = PlayerState.LOADING
        assert player.is_playing is True

    def test_false_when_idle(self):
        from linux_voice_assistant.player.state import PlayerState
        player = make_player()
        player._mock.state.return_value = PlayerState.IDLE
        assert player.is_playing is False


# ---------------------------------------------------------------------------
# set_volume() / duck() / unduck()
# ---------------------------------------------------------------------------


class TestVolume:
    def test_set_volume_delegates_to_lib_player(self):
        player = make_player()
        player.set_volume(75.0)
        player._mock.set_volume.assert_called_once_with(75.0)

    def test_duck_delegates_to_lib_player(self):
        player = make_player()
        player.duck(factor=0.3)
        player._mock.duck.assert_called_once_with(0.3)

    def test_duck_default_factor(self):
        player = make_player()
        player.duck()
        player._mock.duck.assert_called_once_with(0.5)

    def test_unduck_delegates_to_lib_player(self):
        player = make_player()
        player.unduck()
        player._mock.unduck.assert_called_once()