"""Unit tests for LibMpvPlayer end-file completion handling."""

import sys
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from linux_voice_assistant.player.state import PlayerState


@pytest.fixture
def player():
    """Return a LibMpvPlayer with mpv.MPV mocked out."""
    mock_mpv_mod = MagicMock()
    mock_instance = MagicMock()
    mock_mpv_mod.MPV.return_value = mock_instance

    previous_mpv = sys.modules.get("mpv")
    previous_libmpv = sys.modules.get("linux_voice_assistant.player.libmpv")

    # Ensure `import mpv` succeeds even without libmpv installed locally.
    with patch.dict(sys.modules, {"mpv": mock_mpv_mod}):
        sys.modules.pop("linux_voice_assistant.player.libmpv", None)
        from linux_voice_assistant.player.libmpv import LibMpvPlayer

        lib_player = LibMpvPlayer(device=None)
        lib_player._mock_mpv = mock_instance
        try:
            yield lib_player
        finally:
            # Restore modules so other tests are not affected.
            if previous_libmpv is not None:
                sys.modules["linux_voice_assistant.player.libmpv"] = previous_libmpv
            else:
                sys.modules.pop("linux_voice_assistant.player.libmpv", None)
            if previous_mpv is not None:
                sys.modules["mpv"] = previous_mpv
            else:
                sys.modules.pop("mpv", None)


def _end_file_event(reason: int) -> SimpleNamespace:
    return SimpleNamespace(data=SimpleNamespace(reason=reason))


class TestOnEndFile:
    def test_eof_invokes_done_callback(self, player):
        cb = MagicMock()
        player._done_callback = cb
        player._set_state(PlayerState.PLAYING)

        player._on_end_file(_end_file_event(0))

        cb.assert_called_once()
        assert player.state() == PlayerState.IDLE
        assert player._done_callback is None

    def test_error_invokes_done_callback(self, player):
        """Empty/0-length TTS often ends as ERROR (reason=4); must still complete."""
        cb = MagicMock()
        player._done_callback = cb
        player._set_state(PlayerState.PLAYING)

        player._on_end_file(_end_file_event(4))

        cb.assert_called_once()
        assert player.state() == PlayerState.IDLE
        assert player._done_callback is None

    def test_stop_does_not_invoke_done_callback(self, player):
        cb = MagicMock()
        player._done_callback = cb
        player._set_state(PlayerState.PLAYING)

        player._on_end_file(_end_file_event(1))

        cb.assert_not_called()
        assert player._done_callback is cb

    def test_abort_does_not_invoke_done_callback(self, player):
        cb = MagicMock()
        player._done_callback = cb

        player._on_end_file(_end_file_event(2))

        cb.assert_not_called()
        assert player._done_callback is cb

    def test_eof_with_no_callback_sets_idle(self, player):
        player._done_callback = None
        player._set_state(PlayerState.PLAYING)

        player._on_end_file(_end_file_event(0))

        assert player.state() == PlayerState.IDLE
