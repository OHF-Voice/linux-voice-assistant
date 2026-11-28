"""Tests for the Sendspin control socket client."""

import logging
import socket
import threading
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from linux_voice_assistant.sendspin_player import SendspinPlayer, SendspinStatus, parse_status


class FakePlayer:
    """Unix socket server that answers like sendspin-cli and records the requests."""

    def __init__(self, path: Path, state: str = "playing", volume: str = "80", muted: str = "0") -> None:
        self.requests: list = []
        self.state = state
        self.volume = volume
        self.muted = muted
        self.refuse = False
        self._server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._server.bind(str(path))
        self._server.listen(4)
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def _serve(self) -> None:
        while True:
            try:
                conn, _ = self._server.accept()
            except OSError:
                return
            with conn:
                request = conn.makefile().readline().strip()
                self.requests.append(request)
                if self.refuse:
                    conn.sendall(b"error failed: no server\n")
                    continue
                reply = "ok\n"
                if request == "status-kv":
                    reply += f"version=1\nname=test\nstate={self.state}\ngroup_volume={self.volume}\ngroup_muted={self.muted}\n"
                conn.sendall(reply.encode())

    def commands(self) -> list:
        return [request for request in self.requests if request != "status-kv"]

    def close(self) -> None:
        self._server.close()


@pytest.fixture(name="fake")
def fixture_fake(tmp_path):
    player = FakePlayer(tmp_path / "control.sock")
    yield player
    player.close()


def test_parse_status_reads_state_volume_and_mute():
    assert parse_status(["version=1", "state=playing", "group_volume=55", "group_muted=1"]) == SendspinStatus(state="playing", volume=55, muted=True)


def test_parse_status_counts_only_playing_as_playing():
    assert parse_status(["state=playing (speed 500/1000)"]).state == "playing"
    assert parse_status(["state=paused"]).state == "idle"
    assert parse_status(["state=unknown"]).state == "idle"
    assert parse_status([]).state == "idle"


def test_parse_status_without_group_volume():
    status = parse_status(["state=playing", "group_volume=", "group_muted="])
    assert status.volume is None
    assert status.muted is False


def test_status_is_read_from_the_socket(tmp_path, fake):
    fake.muted = "1"
    assert SendspinPlayer(tmp_path / "control.sock").status() == SendspinStatus(state="playing", volume=80, muted=True)


def test_commands_are_sent(tmp_path, fake):
    player = SendspinPlayer(tmp_path / "control.sock")
    player.play()
    player.pause_playback()
    player.stop()
    player.set_volume(130)
    player.set_muted(True)
    player.set_muted(False)

    assert fake.commands() == ["play", "pause", "stop", "vol 100", "mute on", "mute off"]


def test_refused_command_counts_as_no_status(tmp_path, fake):
    fake.refuse = True
    assert SendspinPlayer(tmp_path / "control.sock").status() is None


def test_duck_mode_lowers_and_restores_group_volume(tmp_path, fake):
    player = SendspinPlayer(tmp_path / "control.sock", duck_mode="duck")
    player.duck(owner="conversation")
    player.duck(owner="timer")
    assert player.is_ducking

    player.unduck(owner="conversation")
    assert fake.commands() == ["vol 40"]

    player.unduck(owner="timer")
    assert fake.commands() == ["vol 40", "vol 80"]
    assert not player.is_ducking


def test_duck_mode_skips_player_that_does_not_play(tmp_path, fake):
    fake.state = "paused"
    player = SendspinPlayer(tmp_path / "control.sock", duck_mode="duck")
    player.duck()
    player.unduck()

    assert fake.commands() == []


def test_volume_set_while_group_is_ducked_stays_ducked(tmp_path, fake):
    player = SendspinPlayer(tmp_path / "control.sock", duck_mode="duck")
    player.duck()
    player.set_volume(60)
    player.unduck()

    assert fake.commands() == ["vol 40", "vol 30", "vol 60"]


def test_stop_mode_stops_and_starts_the_player(tmp_path, fake):
    player = SendspinPlayer(tmp_path / "control.sock", duck_mode="stop")
    player.duck()
    assert player.is_ducking
    player.unduck()

    assert fake.commands() == ["stop", "play"]
    assert not player.is_ducking


def test_stop_mode_leaves_player_alone_that_does_not_play(tmp_path, fake):
    fake.state = "paused"
    player = SendspinPlayer(tmp_path / "control.sock", duck_mode="stop")
    player.duck()
    player.unduck()

    assert fake.commands() == []


def test_pulse_mode_ducks_the_stream_and_not_the_group(tmp_path, fake):
    stream_ducker = MagicMock()
    player = SendspinPlayer(tmp_path / "control.sock", duck_mode="pulse", stream_ducker=stream_ducker)
    player.duck(owner="conversation")
    player.duck(owner="timer")
    stream_ducker.duck.assert_called_once_with()
    assert player.is_ducking

    player.unduck(owner="conversation")
    stream_ducker.unduck.assert_not_called()
    player.unduck(owner="timer")
    stream_ducker.unduck.assert_called_once_with()

    assert fake.commands() == []
    assert not player.is_ducking


def test_pulse_mode_leaves_a_player_alone_that_does_not_play(tmp_path, fake, caplog):
    fake.state = "paused"
    stream_ducker = MagicMock()
    player = SendspinPlayer(tmp_path / "control.sock", duck_mode="pulse", stream_ducker=stream_ducker)
    with caplog.at_level(logging.DEBUG, logger="linux_voice_assistant.sendspin_player"):
        player.duck()
    player.unduck()

    assert "Sendspin not ducked now: the player does not play" in caplog.text
    stream_ducker.duck.assert_not_called()
    stream_ducker.unduck.assert_not_called()


def test_unduck_of_an_unknown_owner_changes_nothing(tmp_path):
    stream_ducker = MagicMock()
    player = SendspinPlayer(tmp_path / "control.sock", duck_mode="pulse", stream_ducker=stream_ducker)
    player.duck(owner="conversation")
    player.unduck(owner="timer")

    stream_ducker.unduck.assert_not_called()


def test_duck_if_requested_ducks_playback_that_started_later(tmp_path, fake):
    fake.state = "paused"
    player = SendspinPlayer(tmp_path / "control.sock", duck_mode="stop")
    player.duck()
    fake.state = "playing"
    player._duck_if_requested()  # pylint: disable=protected-access

    assert fake.commands() == ["stop"]


def test_duck_persistence_restores_volume_left_ducked(tmp_path, fake):
    fake.volume = "40"
    saved = MagicMock()
    player = SendspinPlayer(tmp_path / "control.sock", duck_mode="duck")
    player.set_duck_persistence(saved, (80, 40))
    player._recover_duck()  # pylint: disable=protected-access

    assert fake.commands() == ["vol 80"]
    saved.assert_called_once_with(None, None)


def test_duck_persistence_leaves_volume_alone_that_was_changed(tmp_path, fake, caplog):
    fake.volume = "65"
    saved = MagicMock()
    player = SendspinPlayer(tmp_path / "control.sock", duck_mode="duck")
    player.set_duck_persistence(saved, (80, 40))
    with caplog.at_level(logging.DEBUG, logger="linux_voice_assistant.sendspin_player"):
        player._recover_duck()  # pylint: disable=protected-access

    assert fake.commands() == []
    saved.assert_called_once_with(None, None)
    assert "Not restoring the Sendspin group volume: it is 65, an earlier run ducked it from 80 to 40" in caplog.text


def test_unreachable_socket_is_logged_once(tmp_path, caplog):
    player = SendspinPlayer(tmp_path / "missing.sock", duck_mode="stop")
    with caplog.at_level(logging.DEBUG, logger="linux_voice_assistant.sendspin_player"):
        player.duck()
        player.unduck()
        assert player.status() is None

    critical = [record for record in caplog.records if record.levelno == logging.CRITICAL]
    assert len(critical) == 1
    assert "is not reachable" in critical[0].getMessage()


def test_reachable_again_is_logged(tmp_path, caplog):
    player = SendspinPlayer(tmp_path / "control.sock")
    player.status()
    fake = FakePlayer(tmp_path / "control.sock")
    try:
        with caplog.at_level(logging.INFO, logger="linux_voice_assistant.sendspin_player"):
            player.status()
    finally:
        fake.close()

    assert f"Sendspin control socket {tmp_path / 'control.sock'} is reachable" in caplog.text


def test_poll_interval_slows_down_while_unreachable(tmp_path, monkeypatch):
    player = SendspinPlayer(tmp_path / "missing.sock")
    assert player._poll_interval() == 1.0  # pylint: disable=protected-access

    now = 1000.0
    monkeypatch.setattr("linux_voice_assistant.sendspin_player.time.monotonic", lambda: now)
    player.status()
    for elapsed, interval in ((10, 1.0), (30, 5.0), (120, 30.0), (600, 60.0)):
        now = 1000.0 + elapsed
        assert player._poll_interval() == interval  # pylint: disable=protected-access


def test_pulse_mode_logs_asking_and_restoring_the_stream(tmp_path, fake, caplog):
    player = SendspinPlayer(tmp_path / "control.sock", duck_mode="pulse", stream_ducker=MagicMock())
    with caplog.at_level(logging.DEBUG, logger="linux_voice_assistant.sendspin_player"):
        player.duck()
        player.unduck()

    assert "Sendspin plays, asking the sound server to duck its stream (owners: ['conversation'])" in caplog.text
    assert "Asking the sound server to restore the stream of the Sendspin player" in caplog.text
    assert fake.commands() == []


def test_duck_without_socket_logs_why_nothing_happens(tmp_path, caplog):
    player = SendspinPlayer(tmp_path / "missing.sock", duck_mode="pulse", stream_ducker=MagicMock())
    with caplog.at_level(logging.DEBUG, logger="linux_voice_assistant.sendspin_player"):
        player.duck()

    assert "Sendspin not ducked now: the control socket is not reachable, so it is unknown whether the player plays" in caplog.text
