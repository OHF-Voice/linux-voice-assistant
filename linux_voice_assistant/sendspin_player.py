"""Client for the control socket of a Sendspin player (``sendspin-cli``, e.g. the Music Assistant Local Audio add-on)."""

import logging
import socket
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, Optional, Set, Tuple, Union

CONTROL_SOCKET_NAME = "control.sock"
DUCK_MODE_DUCK = "duck"
DUCK_MODE_STOP = "stop"

_LOGGER = logging.getLogger(__name__)

_TIMEOUT_SECONDS = 1.0
_POLL_INTERVAL_SECONDS = 1.0
# (unreachable for at least this many seconds, poll interval in seconds), longest first
_UNREACHABLE_POLL_STEPS = ((600.0, 60.0), (120.0, 30.0), (30.0, 5.0))


@dataclass(frozen=True)
class SendspinStatus:
    """
    What the Sendspin player reports.
    :param state: ``playing`` or ``idle`` (paused, stopped, unknown or no server connected, the protocol does not tell them apart).
    :param volume: Group volume 0-100, None if unknown.
    :param muted: Whether the group is muted.
    """

    state: str
    volume: Optional[int]
    muted: bool


class SendspinPlayer:
    """
    Ducks, stops and resumes a Sendspin player through its control socket.

    The protocol is one request line per connection. The first reply line is ``ok`` or ``error <kind>: <reason>``,
    a ``status`` reply continues with ``key: value`` lines. The player has no per-endpoint duck, so ducking sets the
    **group** volume (``vol``) and restores the value that ``status`` reported before. In ``stop`` mode ducking stops the
    player instead and starts it again afterwards, the volume is not touched. All methods are thread-safe and
    never raise: an unreachable player only means that nothing is ducked.
    """

    def __init__(self, socket_path: Union[str, Path], duck_mode: str = DUCK_MODE_STOP) -> None:
        """
        :param socket_path: Path of the control socket of the player (``--control-socket`` of ``sendspin-cli``).
        :param duck_mode: ``stop`` stops the player while ducked and starts it afterwards, ``duck`` lowers the group volume.
        """
        self._socket_path = str(socket_path)
        self._lock = threading.Lock()
        self._ducked_from: Optional[int] = None
        self._poll_thread: Optional[threading.Thread] = None
        self._reachable: Optional[bool] = None
        self._unreachable_since: Optional[float] = None
        self._on_duck_changed: Optional[Callable[[Optional[int], Optional[int]], None]] = None
        self._recover: Optional[Tuple[int, int]] = None
        self._duck_owners: Set[str] = set()
        self._duck_factor = 0.5
        self._duck_mode = duck_mode
        self._stopped_by_duck = False

    def set_duck_persistence(self, on_duck_changed: Callable[[Optional[int], Optional[int]], None], saved: Tuple[Optional[int], Optional[int]]) -> None:
        """
        Remember the ducked volume across restarts. A volume saved by an earlier run is restored once the player is reachable.
        :param on_duck_changed: Called with ``(volume before, volume after)`` when ducking starts and with ``(None, None)`` when it ends.
        :param saved: ``(volume before, volume after)`` that an earlier run left behind, ``(None, None)`` if there is none.
        """
        self._on_duck_changed = on_duck_changed
        if saved[0] is not None and saved[1] is not None:
            self._recover = (saved[0], saved[1])

    @property
    def is_ducked(self) -> bool:
        """Whether the group volume is currently lowered or the player stopped by :meth:`duck`."""
        return self._ducked_from is not None or self._stopped_by_duck

    def start_polling(self, callback: Callable[[Optional[SendspinStatus]], None]) -> None:
        """
        Report the status of the player once a second from a daemon thread.
        :param callback: Called with the status, or None while the player is unreachable.
        """
        if self._poll_thread is not None:
            return

        def poll() -> None:
            last: Optional[SendspinStatus] = None
            while True:
                time.sleep(self._poll_interval())
                try:
                    self._recover_duck()
                    self._duck_if_requested()
                    status = self.status()
                    if status != last:
                        _LOGGER.debug("Sendspin status: %s", status)
                        last = status
                    callback(status)
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
            if state != "playing":
                state = "idle"

            return SendspinStatus(state=state, volume=self._parse_volume(raw), muted="(muted)" in raw.get("group volume", ""))

    def play(self) -> None:
        """Start or resume playback."""
        self._send("play")

    def stop(self) -> None:
        """Stop playback."""
        self._send("stop")

    def pause_playback(self) -> None:
        """Pause playback on request of the user. Unlike :meth:`stop_for_announcement` it is not undone by :meth:`resume`."""
        self._send("pause")

    def set_volume(self, percent: int) -> None:
        """
        Set the group volume.
        :param percent: Volume 0-100.
        """
        with self._lock:
            if self._ducked_from is not None:
                self._ducked_from = None
                self._notify_duck(None, None)
            self._send(f"vol {max(0, min(100, percent))}")

    def set_muted(self, muted: bool) -> None:
        """
        Mute or unmute the group.
        :param muted: True to mute.
        """
        self._send("mute on" if muted else "mute off")

    def duck(self, factor: float = 0.5, owner: str = "assistant") -> None:
        """
        Request ducking: lower the group volume to ``factor`` of its current value (or stop the player in ``stop`` mode) while the player is playing.
        The request stays until :meth:`unduck`, so playback that starts later is ducked too.
        :param factor: Fraction of the current group volume, 0.0 to 1.0, not used in ``stop`` mode.
        :param owner: Who asks. The volume is restored when every owner has called :meth:`unduck`.
        """
        with self._lock:
            self._duck_owners.add(owner)
            self._duck_factor = factor
            _LOGGER.debug("Sendspin duck requested by %s (owners: %s)", owner, sorted(self._duck_owners))
            self._apply_duck()

    def unduck(self, owner: str = "assistant") -> None:
        """
        End a ducking request. The group volume saved by :meth:`duck` is restored (or the player started in ``stop`` mode) when no owner is left.
        :param owner: The owner that called :meth:`duck`.
        """
        with self._lock:
            self._duck_owners.discard(owner)
            _LOGGER.debug("Sendspin duck released by %s (owners left: %s, ducked: %s)", owner, sorted(self._duck_owners), self.is_ducked)
            if self._duck_owners:
                return

            if self._stopped_by_duck:
                self._stopped_by_duck = False
                _LOGGER.debug("Sendspin started again")
                self._send("play")
                return

            if self._ducked_from is None:
                return

            volume, self._ducked_from = self._ducked_from, None
            _LOGGER.debug("Sendspin volume restored to %d", volume)
            self._send(f"vol {volume}")
            self._notify_duck(None, None)

    def is_playing(self) -> bool:
        """Whether the server reports the group as playing."""
        with self._lock:
            status = self._status()
            return (status is not None) and (status.get("state") == "playing")

    def _poll_interval(self) -> float:
        """Seconds to wait before the next poll: the longer the socket is unreachable, the less often it is tried."""
        if self._unreachable_since is None:
            return _POLL_INTERVAL_SECONDS

        unreachable_for = time.monotonic() - self._unreachable_since
        for threshold, interval in _UNREACHABLE_POLL_STEPS:
            if unreachable_for >= threshold:
                return interval

        return _POLL_INTERVAL_SECONDS

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

    def _apply_duck(self) -> None:
        """Lower the group volume (or stop the player) if ducking is requested and the player plays. Expects the lock to be held."""
        if self._ducked_from is not None or self._stopped_by_duck:
            return

        if self._duck_mode == DUCK_MODE_STOP:
            status = self._status()
            if (status is not None) and (status.get("state") == "playing") and self._send("stop"):
                _LOGGER.debug("Sendspin stopped for ducking (owners: %s)", sorted(self._duck_owners))
                self._stopped_by_duck = True
            return

        status = self._status()
        volume = self._parse_volume(status)
        if (status is None) or (status.get("state") != "playing") or (volume is None):
            return

        ducked = max(0, min(100, int(volume * self._duck_factor)))
        if self._send(f"vol {ducked}"):
            _LOGGER.debug("Sendspin ducked from %d to %d (owners: %s)", volume, ducked, sorted(self._duck_owners))
            self._ducked_from = volume
            self._notify_duck(volume, ducked)

    def _duck_if_requested(self) -> None:
        """Duck playback that started while ducking was requested."""
        with self._lock:
            if self._duck_owners:
                self._apply_duck()

    def _notify_duck(self, volume_from: Optional[int], volume_to: Optional[int]) -> None:
        if self._on_duck_changed is not None:
            self._on_duck_changed(volume_from, volume_to)

    def _recover_duck(self) -> None:
        """Restore the volume of a ducking that an earlier run did not finish, once the player answers."""
        with self._lock:
            if self._recover is None:
                return

            volume = self._parse_volume(self._status())
            if volume is None:
                return

            volume_from, volume_to = self._recover
            self._recover = None
            if volume == volume_to and volume != volume_from:
                _LOGGER.warning("Restoring the Sendspin group volume to %d, an earlier run left it ducked at %d", volume_from, volume_to)
                self._send(f"vol {volume_from}")
            self._notify_duck(None, None)

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
            if self._reachable is not False:
                _LOGGER.critical(
                    "Sendspin control socket %s is not reachable (%s). Check that the player runs, that the socket is mounted and that it belongs to the user of LVA",
                    self._socket_path,
                    err,
                )
            else:
                _LOGGER.debug("Sendspin control socket %s is still not reachable (%s)", self._socket_path, err)
            self._reachable = False
            if self._unreachable_since is None:
                self._unreachable_since = time.monotonic()
            return None

        if not self._reachable:
            _LOGGER.info("Sendspin control socket %s is reachable", self._socket_path)
        self._reachable = True
        self._unreachable_since = None

        lines = b"".join(chunks).decode("utf-8", errors="replace").splitlines()
        if command != "status":
            _LOGGER.debug("Sendspin command '%s' -> %s", command, lines[0] if lines else "no reply")
        if not lines or lines[0] != "ok":
            _LOGGER.debug("Sendspin player refused '%s': %s", command, lines[0] if lines else "no reply")
            return None

        return lines
