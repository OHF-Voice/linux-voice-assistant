"""
Fake Sendspin player of the end-to-end tests: a control socket that answers like ``sendspin-cli``.

It reports a playing player and records the commands LVA sends, so a test can follow how LVA ducks it. It plays no audio.
"""

import socket
import threading
from pathlib import Path
from typing import List


class FakeSendspinPlayer:
    """Unix socket server at ``<directory>/control.sock`` that answers ``status-kv`` and accepts every other command with ``ok``."""

    def __init__(self, directory: Path, volume: int = 80) -> None:
        """
        :param directory: Directory for ``control.sock``, passed to LVA as ``--sendspin-control-dir``.
        :param volume: Group volume reported until LVA sets another one with ``vol``.
        """
        self.directory = directory
        self.volume = volume
        self._requests: List[str] = []
        self._lock = threading.Lock()
        self._server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._thread = threading.Thread(target=self._serve, daemon=True, name="fake-sendspin")

    def start(self) -> None:
        self._server.bind(str(self.directory / "control.sock"))
        self._server.listen(8)
        self._thread.start()

    def stop(self) -> None:
        self._server.close()

    @property
    def polled(self) -> bool:
        """Whether LVA has asked for the status at least once."""
        with self._lock:
            return "status-kv" in self._requests

    def commands(self) -> List[str]:
        """The commands LVA sent, without the status requests."""
        with self._lock:
            return [request for request in self._requests if request != "status-kv"]

    def _serve(self) -> None:
        while True:
            try:
                connection, _ = self._server.accept()
            except OSError:
                return
            with connection:
                request = connection.makefile().readline().strip()
                with self._lock:
                    self._requests.append(request)
                    if request.startswith("vol "):
                        self.volume = int(request.split()[1])
                    reply = "ok\n"
                    if request == "status-kv":
                        reply += f"version=1\nstate=playing\ngroup_volume={self.volume}\ngroup_muted=0\n"
                connection.sendall(reply.encode())
