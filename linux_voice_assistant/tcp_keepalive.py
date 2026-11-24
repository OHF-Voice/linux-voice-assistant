"""TCP keepalive for client connections.

A Wi-Fi drop or a DHCP renewal can leave a connection half open: Home Assistant has long given up on it, but this side never
receives a FIN or RST. The kernel then keeps the dead connection (and the satellite state attached to it) for hours. Keepalive
probes and a user timeout make the kernel close such a connection after 10 seconds, which runs ``connection_lost``.
"""

import logging
import socket
from dataclasses import dataclass
from typing import Optional

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class KeepaliveSettings:
    """
    Timing of the keepalive probes.
    :param idle_seconds: Silence on the connection before the first probe is sent.
    :param interval_seconds: Time between two probes.
    :param probe_count: Unanswered probes after which the connection is dropped.
    :param user_timeout_ms: Time sent data may stay unacknowledged before the connection is dropped.
    """

    idle_seconds: int
    interval_seconds: int
    probe_count: int
    user_timeout_ms: int


DEFAULT_KEEPALIVE = KeepaliveSettings(idle_seconds=4, interval_seconds=2, probe_count=3, user_timeout_ms=10_000)


def keepalive_socket_options(settings: KeepaliveSettings) -> list[tuple[int, int, int]]:
    """
    Socket options that turn the settings into kernel behavior.
    :param settings: Probe timing.
    :return: ``(level, option, value)`` triples. Options this platform does not have are left out.
    """
    options = [(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)]
    optional = [
        ("TCP_KEEPIDLE", settings.idle_seconds),
        ("TCP_KEEPINTVL", settings.interval_seconds),
        ("TCP_KEEPCNT", settings.probe_count),
        ("TCP_USER_TIMEOUT", settings.user_timeout_ms),
    ]
    for name, value in optional:
        option = getattr(socket, name, None)
        if option is not None:
            options.append((socket.IPPROTO_TCP, option, value))
    return options


def apply_keepalive(sock: Optional[socket.socket], settings: KeepaliveSettings = DEFAULT_KEEPALIVE) -> bool:
    """
    Enable keepalive on a connected TCP socket.
    :param sock: Socket of the connection, or None when the transport has none.
    :param settings: Probe timing.
    :return: True when all options were set. A failure is logged and never raised, the connection works without keepalive.
    """
    if sock is None:
        return False
    try:
        for level, option, value in keepalive_socket_options(settings):
            sock.setsockopt(level, option, value)
    except OSError as err:
        _LOGGER.debug("Could not enable TCP keepalive: %s", err)
        return False
    _LOGGER.debug(
        "TCP keepalive enabled (idle %d s, interval %d s, %d probes, user timeout %d ms)",
        settings.idle_seconds,
        settings.interval_seconds,
        settings.probe_count,
        settings.user_timeout_ms,
    )
    return True
