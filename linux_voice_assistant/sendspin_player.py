"""
Client for the control socket of a Sendspin player (``sendspin-cli``, for example the Music Assistant Local Audio add-on).

The player runs outside LVA, so LVA cannot duck it through mpv. This module talks to it through its control socket:
it reads the status for the media player entity, sends play, pause, stop, volume and mute, and ducks it while the
assistant talks, an announcement plays or a timer rings. How it ducks depends on the duck mode: ``pulse`` lowers only
the sound stream of the player in PulseAudio/PipeWire (see ``pulse_stream_ducker.py``), ``stop`` stops the player and starts it again afterwards, ``duck`` lowers the volume of the
whole Sendspin group.
"""

import logging
import socket
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, List, Optional, Protocol, Set, Tuple, Union

CONTROL_SOCKET_NAME = "control.sock"
DUCK_MODE_PULSE = "pulse"
DUCK_MODE_STOP = "stop"
DUCK_MODE_DUCK = "duck"

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


class StreamDucker(Protocol):
    """Lowers the sound stream of the player while ducking is requested (``pulse`` mode)."""

    def duck(self) -> None:
        """Lower the stream, also one that starts later."""

    def unduck(self) -> None:
        """Restore the stream."""


def parse_status(lines: List[str]) -> SendspinStatus:
    """
    Read the reply of ``status-kv`` (``key=value`` lines after the leading ``ok``).
    :param lines: The reply lines after ``ok``.
    :return: The status. ``state`` is ``playing`` also for ``playing (speed N/1000)``, everything else is ``idle``.
    """
    values: Dict[str, str] = {}
    for line in lines:
        key, separator, value = line.partition("=")
        if separator:
            values[key.strip()] = value.strip()

    state = "playing" if values.get("state", "").startswith("playing") else "idle"
    volume_text = values.get("group_volume", "")
    volume = int(volume_text) if volume_text.isdigit() else None
    return SendspinStatus(state=state, volume=volume, muted=values.get("group_muted") == "1")


class SendspinPlayer:
    """
    Controls a Sendspin player through its control socket.

    The protocol is one request line per connection. The first reply line is ``ok`` or ``error <kind>: <reason>``,
    ``status-kv`` continues with ``key=value`` lines. ``vol`` and ``mute`` address the whole Sendspin **group**.
    All methods are thread-safe and never raise: an unreachable player only means that nothing happens.
    """

    def __init__(self, socket_path: Union[str, Path], duck_mode: str = DUCK_MODE_PULSE, stream_ducker: Optional[StreamDucker] = None) -> None:
        """
        :param socket_path: Path of the control socket of the player (``--control-socket`` of ``sendspin-cli``).
        :param duck_mode: ``pulse`` lowers the sound stream of the player through ``stream_ducker``, ``stop`` stops the player while ducked and starts it afterwards, ``duck`` lowers the group volume.
        :param stream_ducker: Lowers the sound stream in ``pulse`` mode. Not used in the other modes.
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
        # Whether the stream was asked to be ducked (``pulse`` mode)
        self._stream_ducked = False
        self._stream_ducker = stream_ducker
        self._stopped_by_duck = False

    def set_duck_persistence(self, on_duck_changed: Callable[[Optional[int], Optional[int]], None], saved: Tuple[Optional[int], Optional[int]]) -> None:
        """
        Remember the ducked group volume across restarts (``duck`` mode). A volume saved by an earlier run is restored once the player is reachable.
        :param on_duck_changed: Called with ``(volume before, volume after)`` when ducking starts and with ``(None, None)`` when it ends.
        :param saved: ``(volume before, volume after)`` that an earlier run left behind, ``(None, None)`` if there is none.
        """
        self._on_duck_changed = on_duck_changed
        if saved[0] is not None and saved[1] is not None:
            self._recover = (saved[0], saved[1])

    @property
    def is_ducking(self) -> bool:
        """
        Whether ducking is requested or LVA's ducking is still in effect, in every mode. The group volume of the player is then not the real
        volume: LVA lowered it (``duck``), stopped the player (``stop``), or the player passed the lowered stream on to its group (``pulse``).
        """
        with self._lock:
            return bool(self._duck_owners) or self._ducked_from is not None or self._stopped_by_duck

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
            return self._status()

    def play(self) -> None:
        """Start or resume playback."""
        self._send("play")

    def stop(self) -> None:
        """Stop playback."""
        self._send("stop")

    def pause_playback(self) -> None:
        """Pause playback on request of the user."""
        self._send("pause")

    def set_volume(self, percent: int) -> None:
        """
        Set the group volume. While the group is ducked, ``percent`` is the real volume that is restored afterwards, the group stays ducked relative to it.
        :param percent: Volume 0-100.
        """
        percent = max(0, min(100, percent))
        with self._lock:
            if self._ducked_from is not None:
                ducked = max(0, min(100, int(percent * self._duck_factor)))
                if self._send(f"vol {ducked}"):
                    self._ducked_from = percent
                    self._notify_duck(percent, ducked)
                return

            self._send(f"vol {percent}")

    def set_muted(self, muted: bool) -> None:
        """
        Mute or unmute the group.
        :param muted: True to mute.
        """
        self._send("mute on" if muted else "mute off")

    def duck(self, factor: float = 0.5, owner: str = "conversation") -> None:
        """
        Request ducking. The request stays until :meth:`unduck`, so playback that starts later is ducked too.
        :param factor: Fraction of the current group volume in ``duck`` mode, 0.0 to 1.0. Not used in the other modes.
        :param owner: Who asks. Ducking ends when every owner has called :meth:`unduck`.

        Every mode acts only while the player plays.
        """
        with self._lock:
            self._duck_owners.add(owner)
            self._duck_factor = factor
            _LOGGER.debug("Sendspin duck requested by %s (mode %s, owners: %s)", owner, self._duck_mode, sorted(self._duck_owners))
            skipped = self._apply_duck()
            if skipped is not None:
                _LOGGER.debug("Sendspin not ducked now: %s", skipped)

    def unduck(self, owner: str = "conversation") -> None:
        """
        End a ducking request. When no owner is left, the stream or the group volume is restored, or the player is started again in ``stop`` mode.
        :param owner: The owner that called :meth:`duck`.
        """
        with self._lock:
            if owner not in self._duck_owners:
                return
            self._duck_owners.discard(owner)
            _LOGGER.debug("Sendspin duck released by %s (mode %s, owners left: %s)", owner, self._duck_mode, sorted(self._duck_owners))
            if self._duck_owners:
                return

            if self._stream_ducked:
                _LOGGER.debug("Asking the sound server to restore the stream of the Sendspin player")
                self._stream_ducked = False
                if self._stream_ducker is not None:
                    self._stream_ducker.unduck()
                return

            if self._stopped_by_duck:
                self._stopped_by_duck = False
                _LOGGER.debug("Sendspin started again after ducking")
                self._send("play")
                return

            if self._ducked_from is None:
                return

            volume, self._ducked_from = self._ducked_from, None
            _LOGGER.debug("Sendspin group volume restored to %d", volume)
            self._send(f"vol {volume}")
            self._notify_duck(None, None)

    def _poll_interval(self) -> float:
        """Seconds to wait before the next poll: the longer the socket is unreachable, the less often it is tried."""
        if self._unreachable_since is None:
            return _POLL_INTERVAL_SECONDS

        unreachable_for = time.monotonic() - self._unreachable_since
        for threshold, interval in _UNREACHABLE_POLL_STEPS:
            if unreachable_for >= threshold:
                return interval

        return _POLL_INTERVAL_SECONDS

    def _status(self) -> Optional[SendspinStatus]:
        """Read the status. Expects the lock to be held."""
        reply = self._request("status-kv")
        if reply is None:
            return None
        return parse_status(reply[1:])

    def _apply_duck(self) -> Optional[str]:
        """
        Lower the stream (``pulse`` mode), stop the player (``stop`` mode) or lower the group volume (``duck`` mode) if it plays. Expects the lock to be held.
        :return: Why nothing was done, None if it was done or tried.
        """
        if self._ducked_from is not None or self._stopped_by_duck or self._stream_ducked:
            return "already ducked"

        status = self._status()
        if status is None:
            return "the control socket is not reachable, so it is unknown whether the player plays"
        if status.state != "playing":
            return "the player does not play"

        if self._duck_mode == DUCK_MODE_PULSE:
            if self._stream_ducker is None:
                return "no stream ducker"
            _LOGGER.debug("Sendspin plays, asking the sound server to duck its stream (owners: %s)", sorted(self._duck_owners))
            self._stream_ducked = True
            self._stream_ducker.duck()
            return None

        if self._duck_mode == DUCK_MODE_STOP:
            if self._send("stop"):
                _LOGGER.debug("Sendspin stopped for ducking (owners: %s)", sorted(self._duck_owners))
                self._stopped_by_duck = True
            return None

        if status.volume is None:
            return "the group volume is unknown"

        ducked = max(0, min(100, int(status.volume * self._duck_factor)))
        if self._send(f"vol {ducked}"):
            _LOGGER.debug("Sendspin group volume ducked from %d to %d (owners: %s)", status.volume, ducked, sorted(self._duck_owners))
            self._ducked_from = status.volume
            self._notify_duck(status.volume, ducked)
        return None

    def _duck_if_requested(self) -> None:
        """Duck playback that started while ducking was requested."""
        with self._lock:
            if self._duck_owners:
                self._apply_duck()

    def _notify_duck(self, volume_from: Optional[int], volume_to: Optional[int]) -> None:
        if self._on_duck_changed is not None:
            self._on_duck_changed(volume_from, volume_to)

    def _recover_duck(self) -> None:
        """Restore the group volume of a ducking that an earlier run did not finish, once the player answers."""
        with self._lock:
            if self._recover is None:
                return

            status = self._status()
            if status is None or status.volume is None:
                return

            volume_from, volume_to = self._recover
            self._recover = None
            if status.volume == volume_to and status.volume != volume_from:
                _LOGGER.warning("Restoring the Sendspin group volume to %d, an earlier run left it ducked at %d", volume_from, volume_to)
                self._send(f"vol {volume_from}")
            else:
                _LOGGER.debug("Not restoring the Sendspin group volume: it is %d, an earlier run ducked it from %d to %d", status.volume, volume_from, volume_to)
            self._notify_duck(None, None)

    def _send(self, command: str) -> bool:
        return self._request(command) is not None

    def _request(self, command: str) -> Optional[List[str]]:
        """
        :return: The reply lines starting with ``ok``, or None if the player is unreachable or refused the command.
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
        if command != "status-kv":
            _LOGGER.debug("Sendspin command '%s' -> %s", command, lines[0] if lines else "no reply")
        if not lines or lines[0] != "ok":
            _LOGGER.debug("Sendspin player refused '%s': %s", command, lines[0] if lines else "no reply")
            return None

        return lines
