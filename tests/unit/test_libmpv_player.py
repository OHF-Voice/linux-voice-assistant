"""Unit tests for LibMpvPlayer."""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from linux_voice_assistant.player.state import PlayerState


@pytest.fixture
def player():
    """Return a LibMpvPlayer with mpv.MPV mocked out."""
    with patch("linux_voice_assistant.player.libmpv.mpv.MPV", return_value=MagicMock()):
        from linux_voice_assistant.player.libmpv import LibMpvPlayer

        yield LibMpvPlayer()


def _end_file_event(reason: int) -> SimpleNamespace:
    return SimpleNamespace(data=SimpleNamespace(reason=reason))


def _wait_for_timer(player) -> None:
    timer = player._eof_timer
    assert timer is not None
    timer.join(timeout=5)


class TestInit:
    def test_demuxer_cache_is_limited_to_16_mib(self):
        with patch("linux_voice_assistant.player.libmpv.mpv.MPV", return_value=MagicMock()) as mock_mpv:
            from linux_voice_assistant.player.libmpv import LibMpvPlayer

            LibMpvPlayer()

        assert mock_mpv.call_args.kwargs["demuxer_max_bytes"] == "16MiB"


class TestOnEndFile:
    def test_eof_invokes_done_callback_after_drain_delay(self, player):
        cb = MagicMock()
        player._done_callback = cb
        player._set_state(PlayerState.PLAYING)

        with patch("linux_voice_assistant.player.libmpv._EOF_DRAIN_DELAY_SECS", 0.01):
            player._on_end_file(_end_file_event(0))
            cb.assert_not_called()
            _wait_for_timer(player)

        cb.assert_called_once()
        assert player.state() == PlayerState.IDLE
        assert player._done_callback is None

    def test_error_invokes_done_callback_without_drain_delay(self, player):
        """Empty/0-length TTS often ends as ERROR (reason=4); must still complete."""
        cb = MagicMock()
        player._done_callback = cb
        player._set_state(PlayerState.PLAYING)

        with patch("linux_voice_assistant.player.libmpv._EOF_DRAIN_DELAY_SECS", 60):
            player._on_end_file(_end_file_event(4))
            _wait_for_timer(player)

        cb.assert_called_once()
        assert player.state() == PlayerState.IDLE
        assert player._done_callback is None

    @pytest.mark.parametrize("reason", [1, 2, 3])
    def test_interruptions_do_not_invoke_done_callback(self, player, reason):
        cb = MagicMock()
        player._done_callback = cb
        player._set_state(PlayerState.PLAYING)

        player._on_end_file(_end_file_event(reason))

        assert player._eof_timer is None
        cb.assert_not_called()
        assert player._done_callback is cb

    def test_eof_with_no_callback_sets_idle(self, player):
        player._done_callback = None
        player._set_state(PlayerState.PLAYING)

        with patch("linux_voice_assistant.player.libmpv._EOF_DRAIN_DELAY_SECS", 0.01):
            player._on_end_file(_end_file_event(0))
            _wait_for_timer(player)

        assert player.state() == PlayerState.IDLE
