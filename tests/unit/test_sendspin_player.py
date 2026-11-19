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
