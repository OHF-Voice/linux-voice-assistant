"""Tests for the libmpv player wrapper."""

from unittest.mock import patch

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
