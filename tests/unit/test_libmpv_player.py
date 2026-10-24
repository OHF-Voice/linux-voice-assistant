"""Unit tests for LibMpvPlayer."""

from unittest.mock import MagicMock, patch


class TestInit:
    def test_demuxer_cache_is_limited_to_16_mib(self):
        with patch("linux_voice_assistant.player.libmpv.mpv.MPV", return_value=MagicMock()) as mock_mpv:
            from linux_voice_assistant.player.libmpv import LibMpvPlayer

            LibMpvPlayer()

        assert mock_mpv.call_args.kwargs["demuxer_max_bytes"] == "16MiB"
