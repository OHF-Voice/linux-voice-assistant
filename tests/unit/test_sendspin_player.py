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
    player = SendspinPlayer(tmp_path / "control.sock")
    player.duck()
    player.duck()  # second duck must not re-read the ducked volume
    player.unduck()
    player.unduck()

    assert [r for r in fake.requests if r != "status"] == ["vol 40", "vol 80"]
    assert fake.requests.count("status") == 1


def test_duck_skips_player_that_is_not_playing(tmp_path, fake):
    fake.state = "paused"
    player = SendspinPlayer(tmp_path / "control.sock")
    player.duck()
    player.unduck()

    assert fake.requests == ["status"]


def test_duck_parses_muted_volume(tmp_path, fake):
    fake.volume = "60 (muted)"
    SendspinPlayer(tmp_path / "control.sock").duck()

    assert "vol 30" in fake.requests


def test_pause_resume_only_when_paused_by_us(tmp_path, fake):
    player = SendspinPlayer(tmp_path / "control.sock")
    assert player.pause() is True
    player.resume()
    player.resume()

    assert [r for r in fake.requests if r != "status"] == ["pause", "play"]

    fake.requests.clear()
    fake.state = "paused"
    assert player.pause() is False
    player.resume()
    assert fake.requests == ["status"]


def test_unreachable_socket_is_ignored(tmp_path):
    player = SendspinPlayer(tmp_path / "missing.sock")
    player.duck()
    player.unduck()
    player.resume()

    assert player.pause() is False
    assert player.is_playing() is False


def test_status_parses_state_volume_and_mute(tmp_path, fake):
    fake.state = "paused"
    fake.volume = "60 (muted)"
    status = SendspinPlayer(tmp_path / "control.sock").status()

    assert (status.state, status.volume, status.muted) == ("paused", 60, True)


def test_status_unknown_state_is_idle_and_unreachable_is_none(tmp_path, fake):
    fake.state = "unknown"
    assert SendspinPlayer(tmp_path / "control.sock").status().state == "idle"
    assert SendspinPlayer(tmp_path / "missing.sock").status() is None


def test_status_reports_announcement_pause_as_playing(tmp_path, fake):
    player = SendspinPlayer(tmp_path / "control.sock")
    player.pause()
    fake.state = "paused"

    assert player.status().state == "playing"


def test_commands_are_sent(tmp_path, fake):
    player = SendspinPlayer(tmp_path / "control.sock")
    player.play()
    player.stop()
    player.pause_playback()
    player.set_volume(150)
    player.set_muted(True)
    player.set_muted(False)

    assert fake.requests == ["play", "stop", "pause", "vol 100", "mute on", "mute off"]
