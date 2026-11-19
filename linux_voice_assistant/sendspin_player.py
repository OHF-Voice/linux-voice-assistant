"""Client for the control socket of a Sendspin player (``sendspin-cli``, e.g. the Music Assistant Local Audio add-on)."""

import logging
import socket
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, Optional, Union

_LOGGER = logging.getLogger(__name__)

_TIMEOUT_SECONDS = 1.0
_POLL_INTERVAL_SECONDS = 1.0


@dataclass(frozen=True)
class SendspinStatus:
    """
    What the Sendspin player reports.
    :param state: ``playing``, ``paused`` or ``idle`` (stopped, unknown or no server connected).
    :param volume: Group volume 0-100, None if unknown.
    :param muted: Whether the group is muted.
    """

    state: str
    volume: Optional[int]
    muted: bool


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
        self._poll_thread: Optional[threading.Thread] = None

    @property
    def is_ducked(self) -> bool:
        """Whether the group volume is currently lowered by :meth:`duck`."""
        return self._ducked_from is not None

    def start_polling(self, callback: Callable[[Optional[SendspinStatus]], None]) -> None:
        """
        Report the status of the player once a second from a daemon thread.
        :param callback: Called with the status, or None while the player is unreachable. A player paused by
            :meth:`pause` is reported as ``playing``, so an announcement does not show up as a pause.
        """
        if self._poll_thread is not None:
            return

        def poll() -> None:
            while True:
                time.sleep(_POLL_INTERVAL_SECONDS)
                try:
                    callback(self.status())
                except Exception:  # pylint: disable=broad-exception-caught
                    _LOGGER.exception("Error in Sendspin status callback")

        self._poll_thread = threading.Thread(target=poll, daemon=True, name="sendspin-poll")
        self._poll_thread.start()

    def status(self) -> Optional[SendspinStatus]:
        """
        :return: The status of the player, None if it is unreachable.
        """
        with self._lock:
            raw = self._status()
            if raw is None:
                return None

            state = raw.get("state", "idle")
            if self._paused_by_us:
                state = "playing"
            elif state not in ("playing", "paused"):
                state = "idle"

            return SendspinStatus(state=state, volume=self._parse_volume(raw), muted="(muted)" in raw.get("group volume", ""))

    def play(self) -> None:
        """Start or resume playback."""
        self._paused_by_us = False
        self._send("play")

    def stop(self) -> None:
        """Stop playback."""
        self._paused_by_us = False
        self._send("stop")

    def pause_playback(self) -> None:
        """Pause playback on request of the user. Unlike :meth:`pause` it is not undone by :meth:`resume`."""
        self._paused_by_us = False
        self._send("pause")

    def set_volume(self, percent: int) -> None:
        """
        Set the group volume.
        :param percent: Volume 0-100.
        """
        with self._lock:
            self._ducked_from = None
            self._send(f"vol {max(0, min(100, percent))}")

    def set_muted(self, muted: bool) -> None:
        """
        Mute or unmute the group.
        :param muted: True to mute.
        """
        self._send("mute on" if muted else "mute off")

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
