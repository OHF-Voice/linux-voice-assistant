"""Tests for the Sendspin control socket client."""

import socket
import threading
from pathlib import Path

import pytest

from linux_voice_assistant.sendspin_player import SendspinPlayer


class FakePlayer:
    """Unix socket server that answers like sendspin-cli and records the requests."""

    def __init__(self, path: Path, state: str = "playing", volume: str = "80") -> None:
        self.requests: list = []
        self.state = state
        self.volume = volume
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
                reply = "ok\n"
                if request == "status":
                    reply += f"name: test\nstate: {self.state}\ngroup volume: {self.volume}\n"
                conn.sendall(reply.encode())

    def close(self) -> None:
        self._server.close()


@pytest.fixture(name="fake")
def fixture_fake(tmp_path):
    player = FakePlayer(tmp_path / "control.sock")
    yield player
    player.close()


def test_duck_and_unduck_restore_volume(tmp_path, fake):
    player = SendspinPlayer(tmp_path / "control.sock", duck_mode="duck")
    player.duck()
    player.duck()  # second duck must not re-read the ducked volume
    player.unduck()
    player.unduck()

    assert [r for r in fake.requests if r != "status"] == ["vol 40", "vol 80"]
    assert fake.requests.count("status") == 1


def test_duck_skips_player_that_is_not_playing(tmp_path, fake):
    fake.state = "paused"
    player = SendspinPlayer(tmp_path / "control.sock", duck_mode="duck")
    player.duck()
    player.unduck()

    assert fake.requests == ["status"]


def test_duck_parses_muted_volume(tmp_path, fake):
    fake.volume = "60 (muted)"
    SendspinPlayer(tmp_path / "control.sock", duck_mode="duck").duck()

    assert "vol 30" in fake.requests


def test_unreachable_socket_is_ignored(tmp_path):
    player = SendspinPlayer(tmp_path / "missing.sock")
    player.duck()
    player.unduck()

    assert player.is_playing() is False


def test_status_parses_state_volume_and_mute(tmp_path, fake):
    fake.state = "paused"
    fake.volume = "60 (muted)"
    status = SendspinPlayer(tmp_path / "control.sock").status()

    assert (status.state, status.volume, status.muted) == ("idle", 60, True)


def test_status_unknown_state_is_idle_and_unreachable_is_none(tmp_path, fake):
    fake.state = "unknown"
    assert SendspinPlayer(tmp_path / "control.sock").status().state == "idle"
    assert SendspinPlayer(tmp_path / "missing.sock").status() is None


def test_commands_are_sent(tmp_path, fake):
    player = SendspinPlayer(tmp_path / "control.sock", duck_mode="duck")
    player.play()
    player.stop()
    player.pause_playback()
    player.set_volume(150)
    player.set_muted(True)
    player.set_muted(False)

    assert fake.requests == ["play", "stop", "pause", "vol 100", "mute on", "mute off"]


@pytest.mark.usefixtures("fake")
def test_duck_state_is_reported_for_persistence(tmp_path):
    player = SendspinPlayer(tmp_path / "control.sock", duck_mode="duck")
    changes = []
    player.set_duck_persistence(lambda a, b: changes.append((a, b)), (None, None))
    player.duck()
    player.unduck()

    assert changes == [(80, 40), (None, None)]


def test_recover_restores_volume_left_ducked(tmp_path, fake):
    fake.volume = "40"
    player = SendspinPlayer(tmp_path / "control.sock", duck_mode="duck")
    changes = []
    player.set_duck_persistence(lambda a, b: changes.append((a, b)), (80, 40))
    player._recover_duck()  # pylint: disable=protected-access

    assert "vol 80" in fake.requests
    assert changes == [(None, None)]


def test_recover_leaves_volume_changed_since_alone(tmp_path, fake):
    fake.volume = "55"
    player = SendspinPlayer(tmp_path / "control.sock", duck_mode="duck")
    changes = []
    player.set_duck_persistence(lambda a, b: changes.append((a, b)), (80, 40))
    player._recover_duck()  # pylint: disable=protected-access

    assert not [r for r in fake.requests if r.startswith("vol")]
    assert changes == [(None, None)]


def test_recover_waits_until_player_is_reachable(tmp_path):
    player = SendspinPlayer(tmp_path / "missing.sock")
    changes = []
    player.set_duck_persistence(lambda a, b: changes.append((a, b)), (80, 40))
    player._recover_duck()  # pylint: disable=protected-access

    assert not changes


def test_reachability_is_logged_once_per_change(tmp_path, caplog):
    caplog.set_level("INFO", logger="linux_voice_assistant.sendspin_player")
    player = SendspinPlayer(tmp_path / "missing.sock")
    player.status()
    player.status()

    assert len([r for r in caplog.records if r.levelname == "CRITICAL"]) == 1


@pytest.mark.parametrize(
    "unreachable_for, interval",
    [(0, 1.0), (29, 1.0), (30, 5.0), (119, 5.0), (120, 30.0), (599, 30.0), (600, 60.0), (3600, 60.0)],
)
def test_poll_interval_grows_while_the_socket_is_unreachable(tmp_path, monkeypatch, unreachable_for, interval):
    now = 1000.0
    monkeypatch.setattr("linux_voice_assistant.sendspin_player.time.monotonic", lambda: now)
    player = SendspinPlayer(tmp_path / "missing.sock")
    assert player._poll_interval() == 1.0  # pylint: disable=protected-access
    player.status()

    now += unreachable_for
    assert player._poll_interval() == interval  # pylint: disable=protected-access


def test_poll_interval_is_reset_when_the_socket_is_reachable_again(tmp_path, monkeypatch):
    now = 1000.0
    monkeypatch.setattr("linux_voice_assistant.sendspin_player.time.monotonic", lambda: now)
    path = tmp_path / "control.sock"
    player = SendspinPlayer(path)
    player.status()
    now += 700
    assert player._poll_interval() == 60.0  # pylint: disable=protected-access

    fake = FakePlayer(path)
    try:
        player.status()
    finally:
        fake.close()
    assert player._poll_interval() == 1.0  # pylint: disable=protected-access


def test_playback_that_starts_while_ducking_is_requested_gets_ducked(tmp_path, fake):
    fake.state = "idle"
    player = SendspinPlayer(tmp_path / "control.sock", duck_mode="duck")
    player.duck()
    assert not player.is_ducked

    fake.state = "playing"
    player._duck_if_requested()  # pylint: disable=protected-access
    assert player.is_ducked
    assert "vol 40" in fake.requests

    player.unduck()
    assert not player.is_ducked
    assert fake.requests[-1] == "vol 80"


def test_no_late_ducking_after_unduck(tmp_path, fake):
    fake.state = "idle"
    player = SendspinPlayer(tmp_path / "control.sock", duck_mode="duck")
    player.duck()
    player.unduck()
    fake.state = "playing"
    player._duck_if_requested()  # pylint: disable=protected-access

    assert not player.is_ducked
    assert not [r for r in fake.requests if r.startswith("vol")]


def test_volume_is_restored_when_the_last_owner_unducks(tmp_path, fake):
    player = SendspinPlayer(tmp_path / "control.sock", duck_mode="duck")
    player.duck(owner="assistant")
    player.duck(owner="announcement")
    player.unduck(owner="announcement")
    assert player.is_ducked
    assert "vol 80" not in fake.requests

    player.unduck(owner="assistant")
    assert not player.is_ducked
    assert fake.requests[-1] == "vol 80"


def test_stop_mode_stops_and_starts_without_touching_the_volume(tmp_path, fake):
    player = SendspinPlayer(tmp_path / "control.sock", duck_mode="stop")
    player.duck()
    assert "stop" in fake.requests
    assert not any(request.startswith("vol") for request in fake.requests)
    assert player.is_ducked

    player.unduck()
    assert fake.requests[-1] == "play"
    assert not player.is_ducked


def test_stop_mode_leaves_a_player_that_is_not_playing_alone(tmp_path, fake):
    fake.state = "idle"
    player = SendspinPlayer(tmp_path / "control.sock", duck_mode="stop")
    player.duck()
    player.unduck()
    assert "stop" not in fake.requests
    assert "play" not in fake.requests


def test_stop_mode_starts_after_the_last_owner(tmp_path, fake):
    player = SendspinPlayer(tmp_path / "control.sock", duck_mode="stop")
    player.duck(owner="conversation")
    player.duck(owner="timer")
    player.unduck(owner="conversation")
    assert "play" not in fake.requests
    player.unduck(owner="timer")
    assert fake.requests[-1] == "play"


def test_volume_set_while_ducked_stays_ducked_and_is_restored(tmp_path, fake):
    changes = []
    player = SendspinPlayer(tmp_path / "control.sock", duck_mode="duck")
    player.set_duck_persistence(lambda before, after: changes.append((before, after)), (None, None))
    player.duck()
    player.set_volume(60)
    assert fake.requests[-1] == "vol 30"
    assert player.is_ducked
    assert changes[-1] == (60, 30)

    player.unduck()
    assert fake.requests[-1] == "vol 60"
    assert not player.is_ducked
