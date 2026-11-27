"""Tests for creating and starting the Sendspin bridge."""

import json
from pathlib import Path
from unittest.mock import MagicMock

from linux_voice_assistant.models import Preferences
from linux_voice_assistant.pulse_stream_ducker import PulseStreamDucker, StreamDuck
from linux_voice_assistant.sendspin_bridge import create_sendspin_player, start_sendspin_bridge
from tests.unit.conftest import make_state


def test_bridge_off_creates_nothing():
    assert create_sendspin_player(False, "/run/sendspin-cli/socket", "pulse", Preferences()) == (None, None)


def test_pulse_mode_creates_a_stream_ducker_with_the_saved_ducking():
    player, stream_ducker = create_sendspin_player(True, "/run/sendspin-cli/socket", "pulse", Preferences(sendspin_stream_ducked_from=0.8, sendspin_stream_ducked_to=0.4))

    assert player is not None
    assert player._socket_path == str(Path("/run/sendspin-cli/socket/control.sock"))  # pylint: disable=protected-access
    assert isinstance(stream_ducker, PulseStreamDucker)
    assert stream_ducker._duck == StreamDuck(original=0.8, ducked=0.4)  # pylint: disable=protected-access


def test_stop_and_duck_mode_have_no_stream_ducker():
    for mode in ("stop", "duck"):
        player, stream_ducker = create_sendspin_player(True, "/tmp", mode, Preferences())
        assert player is not None
        assert stream_ducker is None


def test_start_without_player_does_nothing(tmp_path):
    assert start_sendspin_bridge(make_state(tmp_path), None) is None


def test_start_polls_and_saves_the_group_ducking(tmp_path):
    player = MagicMock()
    state = make_state(tmp_path, sendspin_player=player)
    state.preferences.sendspin_ducked_from = 80
    state.preferences.sendspin_ducked_to = 40

    assert start_sendspin_bridge(state, None) is None

    player.start_polling.assert_called_once()
    save, saved = player.set_duck_persistence.call_args.args
    assert saved == (80, 40)
    save(None, None)
    assert Preferences.from_dict(json.loads(state.preferences_path.read_text())).sendspin_ducked_from is None


async def test_start_runs_the_stream_ducker_and_saves_its_ducking(tmp_path):
    state = make_state(tmp_path, sendspin_player=MagicMock())
    stream_ducker = MagicMock()
    assert start_sendspin_bridge(state, stream_ducker) is stream_ducker.start.return_value

    save = stream_ducker.start.call_args.args[0]
    save(StreamDuck(original=0.8, ducked=0.4))
    assert (state.preferences.sendspin_stream_ducked_from, state.preferences.sendspin_stream_ducked_to) == (0.8, 0.4)
    save(None)
    assert (state.preferences.sendspin_stream_ducked_from, state.preferences.sendspin_stream_ducked_to) == (None, None)
