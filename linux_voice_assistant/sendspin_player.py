"""Client for the control socket of a Sendspin player (``sendspin-cli``, e.g. the Music Assistant Local Audio add-on)."""

import logging
import socket
import threading
from pathlib import Path
from typing import Dict, Optional, Union

_LOGGER = logging.getLogger(__name__)

_TIMEOUT_SECONDS = 1.0


class SendspinPlayer:
    """
    Ducks, pauses and resumes a Sendspin player through its control socket.

    The protocol is one request line per connection. The first reply line is ``ok`` or ``error <kind>: <reason>``,
    a ``status`` reply continues with ``key: value`` lines. The player has no per-endpoint duck, so ducking sets the
    **group** volume (``vol``) and restores the value that ``status`` reported before. All methods are thread-safe and
    never raise: an unreachable player only means that nothing is ducked.
    """

    def __init__(self, socket_path: Union[str, Path]) -> None:
        """
        :param socket_path: Path of the control socket of the player (``--control-socket`` of ``sendspin-cli``).
        """
        self._socket_path = str(socket_path)
        self._lock = threading.Lock()
        self._ducked_from: Optional[int] = None
        self._paused_by_us = False

    def duck(self, factor: float = 0.5) -> None:
        """
        Lower the group volume to ``factor`` of its current value while the player is playing.
        :param factor: Fraction of the current group volume, 0.0 to 1.0.
        """
        with self._lock:
            if self._ducked_from is not None:
                return

            status = self._status()
            volume = self._parse_volume(status)
            if (status is None) or (status.get("state") != "playing") or (volume is None):
                return

            if self._send(f"vol {max(0, min(100, int(volume * factor)))}"):
                self._ducked_from = volume

    def unduck(self) -> None:
        """Restore the group volume saved by :meth:`duck`. Does nothing if the player was not ducked."""
        with self._lock:
            if self._ducked_from is None:
                return

            volume, self._ducked_from = self._ducked_from, None
            self._send(f"vol {volume}")

    def pause(self) -> bool:
        """
        Pause the player if it is playing.
        :return: True if the player was playing and is paused now, so that :meth:`resume` has to undo it.
        """
        with self._lock:
            status = self._status()
            if (status is None) or (status.get("state") != "playing"):
                return False

            self._paused_by_us = self._send("pause")
            return self._paused_by_us

    def resume(self) -> None:
        """Resume playback paused by :meth:`pause`. Does nothing if the player was not paused by it."""
        with self._lock:
            if self._paused_by_us:
                self._paused_by_us = False
                self._send("play")

    def is_playing(self) -> bool:
        """Whether the server reports the group as playing."""
        with self._lock:
            status = self._status()
            return (status is not None) and (status.get("state") == "playing")

    def _status(self) -> Optional[Dict[str, str]]:
        reply = self._request("status")
        if reply is None:
            return None

        status: Dict[str, str] = {}
        for line in reply[1:]:
            key, separator, value = line.partition(":")
            if separator:
                status[key.strip()] = value.strip()

        return status

    @staticmethod
    def _parse_volume(status: Optional[Dict[str, str]]) -> Optional[int]:
        if status is None:
            return None

        # The value reads `55` or `55 (muted)`
        words = status.get("group volume", "").split()
        return int(words[0]) if words and words[0].isdigit() else None

    def _send(self, command: str) -> bool:
        return self._request(command) is not None

    def _request(self, command: str) -> Optional[list]:
        """
        :return: The reply lines after the leading ``ok``, or None if the player is unreachable or refused the command.
        """
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
                sock.settimeout(_TIMEOUT_SECONDS)
                sock.connect(self._socket_path)
                sock.sendall(f"{command}\n".encode("utf-8"))

                chunks = []
                while True:
                    chunk = sock.recv(4096)
                    if not chunk:
                        break
                    chunks.append(chunk)
        except OSError as err:
            _LOGGER.debug("Sendspin control socket %s: %s", self._socket_path, err)
            return None

        lines = b"".join(chunks).decode("utf-8", errors="replace").splitlines()
        if not lines or lines[0] != "ok":
            _LOGGER.debug("Sendspin player refused '%s': %s", command, lines[0] if lines else "no reply")
            return None

        return lines
