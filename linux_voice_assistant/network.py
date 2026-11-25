"""Connection health and address of the ESPHome server.

Home Assistant reaches LVA over one TCP connection and finds it by the IPv4 address of the network interface, announced over mDNS.
A Wi-Fi drop or a DHCP renewal breaks both: the connection can stay half open without anybody noticing, and the interface can get
another address, so the listener and the announcement point to an address nobody uses any more. This module closes dead connections
(TCP keepalive, as far as the kernel supports it) and moves the listener and the announcement to the interface's current address, checked whenever a client
disconnected and every 30 seconds as a fallback for changes that cause no disconnect.
"""

import asyncio
import logging
import socket
from dataclasses import dataclass
from typing import TYPE_CHECKING, Awaitable, Callable, Optional

from .util import get_default_ipv4

if TYPE_CHECKING:
    from .models import ServerState
    from .zeroconf import HomeAssistantZeroconf

_LOGGER = logging.getLogger(__name__)

ADDRESS_CHECK_INTERVAL_SECONDS = 30.0
RETRY_DELAY_INITIAL_SECONDS = 1.0
RETRY_DELAY_CAP_SECONDS = 60.0


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


@dataclass(frozen=True)
class KeepaliveOption:
    """
    One socket option of the keepalive configuration.
    :param name: Name of the option, as in the socket module.
    :param level: Protocol level the option belongs to.
    :param option: Option number, or None when this platform does not have the option.
    :param value: Value to set.
    """

    name: str
    level: int
    option: Optional[int]
    value: int


@dataclass(frozen=True)
class KeepaliveResult:
    """
    The keepalive configuration a socket really got.
    :param applied: Names of the options that were set.
    :param failed: Names of the options that were not set, because the platform lacks them or the kernel refused them.
    """

    applied: tuple[str, ...]
    failed: tuple[str, ...]

    @property
    def active(self) -> bool:
        """True when keepalive is switched on at all. Without the timing options the kernel defaults apply, which take hours."""
        return "SO_KEEPALIVE" in self.applied


def keepalive_socket_options(settings: KeepaliveSettings) -> list[KeepaliveOption]:
    """
    Socket options that turn the settings into kernel behavior.
    :param settings: Probe timing.
    :return: The options, ``SO_KEEPALIVE`` first. An option the platform does not have has ``option`` None.
    """
    options = [KeepaliveOption("SO_KEEPALIVE", socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)]
    timing = [
        ("TCP_KEEPIDLE", settings.idle_seconds),
        ("TCP_KEEPINTVL", settings.interval_seconds),
        ("TCP_KEEPCNT", settings.probe_count),
        ("TCP_USER_TIMEOUT", settings.user_timeout_ms),
    ]
    for name, value in timing:
        options.append(KeepaliveOption(name, socket.IPPROTO_TCP, getattr(socket, name, None), value))
    return options


def apply_keepalive(sock: Optional[socket.socket], settings: KeepaliveSettings = DEFAULT_KEEPALIVE) -> KeepaliveResult:
    """
    Enable keepalive on a connected TCP socket. Every option is set on its own, so one that fails does not stop the others.
    :param sock: Socket of the connection, or None when the transport has none.
    :param settings: Probe timing.
    :return: What was set and what was not. Failures are logged and never raised, the connection works without keepalive.

    How fast a dead connection is closed depends on the options the kernel accepted, see ``KeepaliveResult``.
    """
    options = keepalive_socket_options(settings)
    if sock is None:
        return KeepaliveResult(applied=(), failed=tuple(option.name for option in options))
    applied = []
    failed = []
    for option in options:
        try:
            if option.option is None:
                raise OSError("not available on this platform")
            sock.setsockopt(option.level, option.option, option.value)
            applied.append(option.name)
        except OSError as err:
            failed.append(option.name)
            _LOGGER.debug("TCP keepalive option %s could not be set: %s", option.name, err)
    result = KeepaliveResult(applied=tuple(applied), failed=tuple(failed))
    if not result.failed:
        _LOGGER.debug(
            "TCP keepalive enabled (idle %d s, interval %d s, %d probes, user timeout %d ms)",
            settings.idle_seconds,
            settings.interval_seconds,
            settings.probe_count,
            settings.user_timeout_ms,
        )
    else:
        _LOGGER.warning(
            "TCP keepalive is %s, options set: %s, options not set: %s",
            "on with limits" if result.active else "off",
            ", ".join(result.applied) or "none",
            ", ".join(result.failed),
        )
    return result


def changed_address(*, bound_ip: str, observed_ip: Optional[str]) -> Optional[str]:
    """
    Return the address to move to, if the interface does not have the bound one any more.
    :param bound_ip: Address the listener is bound to.
    :param observed_ip: IPv4 address of the interface now, or None when it has none.
    :return: The new address, or None when it is unchanged or the interface has none.
    """
    if observed_ip is None or observed_ip == bound_ip:
        return None
    return observed_ip


def next_retry_delay(current: float) -> float:
    """
    Delay before the next attempt, doubling up to a cap.
    :param current: Previous delay in seconds, 0 for the first attempt.
    """
    return min(max(current * 2, RETRY_DELAY_INITIAL_SECONDS), RETRY_DELAY_CAP_SECONDS)


def read_interface_ipv4(interface: str) -> Optional[str]:
    """
    Read the first IPv4 address of an interface.
    :param interface: Interface name.
    :return: The address, or None when the interface is gone or has none.
    """
    try:
        address = get_default_ipv4(interface)
    except (ValueError, OSError, KeyError):
        return None
    return address if isinstance(address, str) else None


async def follow_interface_address(
    *,
    interface: str,
    bound_ip: str,
    client_disconnected: asyncio.Event,
    apply: Callable[[str], Awaitable[None]],
    observe: Callable[[str], Optional[str]] = read_interface_ipv4,
    check_interval: float = ADDRESS_CHECK_INTERVAL_SECONDS,
) -> None:
    """
    Check the interface address after every client disconnect and every ``check_interval`` seconds, and apply it if it changed. Runs until cancelled.
    :param interface: Interface the listener address was taken from.
    :param bound_ip: Address the listener is bound to at the start.
    :param client_disconnected: Set whenever a client disconnected.
    :param apply: Moves the listener and the announcement to the given address. If it raises, it is tried again with a growing delay.
    :param observe: Returns the interface's IPv4 address, or None when it has none.
    :param check_interval: Seconds without a disconnect after which the address is checked anyway.

    While the interface has no address, or applying fails, the check is repeated after 1 s, 2 s, 4 s and so on up to 60 s.
    """
    while True:
        try:
            await asyncio.wait_for(client_disconnected.wait(), timeout=check_interval)
            reason = "Client disconnected"
        except asyncio.TimeoutError:
            reason = "Periodic check"
        client_disconnected.clear()
        delay = 0.0
        while True:
            observed_ip = observe(interface)
            if observed_ip is not None:
                new_ip = changed_address(bound_ip=bound_ip, observed_ip=observed_ip)
                if new_ip is None:
                    _LOGGER.debug("%s: interface %s still has %s", reason, interface, bound_ip)
                    break
                _LOGGER.info("%s: interface %s has %s now, listener is on %s", reason, interface, new_ip, bound_ip)
                try:
                    await apply(new_ip)
                    bound_ip = new_ip
                    break
                except Exception as err:  # pylint: disable=broad-except
                    _LOGGER.warning("Moving the ESPHome server to %s failed: %s", new_ip, err)
            else:
                _LOGGER.warning("Network interface %s has no IPv4 address (listener is bound to %s)", interface, bound_ip)
            delay = next_retry_delay(delay)
            _LOGGER.debug("Next network address check in %.0f s", delay)
            await asyncio.sleep(delay)


class EsphomeEndpoint:
    """
    Owns the listening server and the mDNS announcement.
    :param state: Shared state, its ``ip_address`` follows the listener.
    :param bound_ip: Address the server is bound to.
    :param server: Server already listening on ``bound_ip``.
    :param discovery: Announcement already running, or None when mDNS is disabled.
    :param listen: Starts a server on the given address. Raises ``OSError`` when it cannot bind.
    """

    def __init__(
        self,
        *,
        state: "ServerState",
        bound_ip: str,
        server: asyncio.Server,
        discovery: "Optional[HomeAssistantZeroconf]",
        listen: Callable[[str], Awaitable[asyncio.Server]],
    ) -> None:
        self._state = state
        self._bound_ip = bound_ip
        self._server = server
        self._discovery = discovery
        self._listen = listen

    async def apply(self, ip_address: str) -> None:
        """
        Move the listener and the announcement to another address.
        :param ip_address: New address of the network interface.
        :raises OSError: when the new address cannot be bound. Nothing changed then.

        Calling it again after a failure is safe: a listener that already moved is not bound twice.
        """
        if ip_address != self._bound_ip:
            new_server = await self._listen(ip_address)
            old_server, self._server = self._server, new_server
            old_server.close()
            self._bound_ip = ip_address
            self._state.ip_address = ip_address
            _LOGGER.info("ESPHome server now listens on %s", ip_address)
        if self._discovery is not None:
            await self._discovery.update_address(ip_address)

    async def close(self) -> None:
        """Stop listening. Open connections are left to the event loop shutdown."""
        self._server.close()
