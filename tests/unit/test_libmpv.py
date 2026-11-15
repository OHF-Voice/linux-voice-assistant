"""Tests for the libmpv player wrapper."""

from unittest.mock import MagicMock, patch

import pytest

from linux_voice_assistant.player.libmpv import LibMpvPlayer
from linux_voice_assistant.player.state import PlayerState


@pytest.fixture
def player():
    """Return a LibMpvPlayer with mpv mocked out."""
    with patch("linux_voice_assistant.player.libmpv.mpv.MPV"):
        yield LibMpvPlayer()


def test_stop_sets_idle(player):
    player.play("http://example.com/song.mp3")
    player.resume()
    assert player.state() == PlayerState.PLAYING

    player.stop()

    assert player.state() == PlayerState.IDLE


def test_stop_for_replacement_keeps_state(player):
    player.play("http://example.com/song.mp3")
    player.resume()

    player.stop(for_replacement=True)

    assert player.state() == PlayerState.PLAYING


def _eof_event():
    event = MagicMock()
    event.data.reason = 0
    return event


def test_eof_callback_waits_for_audio_to_drain(player):
    callback = MagicMock()
    player.play("http://example.com/tts.mp3", done_callback=callback)
    player._on_start_file(None)

    with patch("linux_voice_assistant.player.libmpv._EOF_DRAIN_DELAY_SECS", 0.2):
        player._on_end_file(_eof_event())

    assert player.state() == PlayerState.PLAYING
    callback.assert_not_called()

    player._eof_timer.join(timeout=2)

    assert player.state() == PlayerState.IDLE
    callback.assert_called_once()


def test_stop_cancels_pending_eof_callback(player):
    callback = MagicMock()
    player.play("http://example.com/tts.mp3", done_callback=callback)
    player._on_end_file(_eof_event())
    timer = player._eof_timer

    player.stop()
    timer.join(timeout=2)

    callback.assert_not_called()
    assert player.state() == PlayerState.IDLE


def test_play_cancels_pending_eof_callback(player):
    first = MagicMock()
    player.play("http://example.com/one.mp3", done_callback=first)
    player._on_end_file(_eof_event())
    timer = player._eof_timer

    player.play("http://example.com/two.mp3", done_callback=MagicMock())
    timer.join(timeout=2)

    first.assert_not_called()
    assert player.state() == PlayerState.LOADING
