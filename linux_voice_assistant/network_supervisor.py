"""Decide when the ESPHome listener must be bound again after a network change."""

import asyncio
import logging
from typing import Callable, Optional

import netifaces

from .util import get_default_ipv4

_LOGGER = logging.getLogger(__name__)

ADDRESS_POLL_INTERVAL_SECONDS = 1.0
BIND_BACKOFF_INITIAL_SECONDS = 1.0
BIND_BACKOFF_CAP_SECONDS = 60.0


def address_needs_rebind(*, bound_ip: Optional[str], observed_ip: Optional[str], saw_gap: bool) -> bool:
    """
    Return whether the listener should be closed and bound again.

    :param bound_ip: Address the listener is bound to.
    :param observed_ip: Address currently configured on the interface.
    :param saw_gap: True after the interface had no IPv4 since the last successful bind.
    """
    if observed_ip is None:
        return True
    if observed_ip != bound_ip:
        return True
    return saw_gap


def select_bind_address(*, fixed_host: Optional[str], observed_ip: Optional[str]) -> Optional[str]:
    """
    Choose the address to bind, or None while the interface is not ready.

    :param fixed_host: Address from --host, or None when the interface address should be used.
    :param observed_ip: Current IPv4 on the selected interface.
    """
    if observed_ip is None:
        return None
    if fixed_host is not None and observed_ip != fixed_host:
        return None
    return fixed_host or observed_ip


def next_backoff_seconds(current: float, *, initial: float = BIND_BACKOFF_INITIAL_SECONDS, cap: float = BIND_BACKOFF_CAP_SECONDS) -> float:
    """
    Return the next wait after a failed bind or a missing address.

    :param current: Previous delay in seconds. Zero starts the sequence.
    :param initial: First delay in seconds.
    :param cap: Maximum delay in seconds.
    """
    if current < initial:
        return initial
    return min(current * 2, cap)


def connection_idle_expired(*, authenticated: bool, last_rx: float, now: float, limit: float) -> bool:
    """
    Return whether an authenticated connection has been silent too long.

    :param authenticated: True after the Home Assistant handshake completed.
    :param last_rx: Monotonic time of the last received frame.
    :param now: Current monotonic time.
    :param limit: Silence limit in seconds.
    """
    if not authenticated:
        return False
    return (now - last_rx) >= limit


def read_interface_ipv4(interface: Optional[str]) -> Optional[str]:
    """
    Return the first IPv4 address on an interface.

    :param interface: Interface name, or None when none was selected.
    """
    if not interface:
        return None
    try:
        if interface not in netifaces.interfaces():
            return None
        address = get_default_ipv4(interface)
        if not isinstance(address, str):
            return None
        return address
    except (ValueError, OSError, KeyError):
        return None


def close_client_transports(connections: list) -> None:
    """
    Close each live client transport so its protocol runs connection_lost.

    :param connections: Protocol objects that may hold a transport.
    """
    for connection in list(connections):
        transport = getattr(connection, "_transport", None)
        if transport is not None:
            transport.close()


async def watch_until_rebind(
    interface: str,
    bound_ip: str,
    observe: Callable[[str], Optional[str]] = read_interface_ipv4,
    interval: float = ADDRESS_POLL_INTERVAL_SECONDS,
) -> None:
    """
    Return when the interface address is missing, changed, or back after a gap.

    :param interface: Interface name the listener was bound from.
    :param bound_ip: IPv4 the listener is currently bound to.
    :param observe: Returns the interface IPv4, or None when it has none.
    :param interval: Seconds between checks.
    """
    saw_gap = False
    while True:
        await asyncio.sleep(interval)
        observed = _safe_observe(observe, interface)
        if observed is None:
            saw_gap = True
        if address_needs_rebind(bound_ip=bound_ip, observed_ip=observed, saw_gap=saw_gap):
            _LOGGER.warning(
                "Network address on %s is unavailable (bound=%s, observed=%s); rebinding listener",
                interface,
                bound_ip,
                observed,
            )
            return


async def wait_for_bind_address(
    interface: str,
    fixed_host: Optional[str],
    observe: Callable[[str], Optional[str]] = read_interface_ipv4,
) -> str:
    """
    Wait until the interface has an address that can be bound.

    :param interface: Interface name to watch.
    :param fixed_host: Required address when --host was set.
    :param observe: Returns the interface IPv4, or None when it has none.
    """
    delay = 0.0
    while True:
        observed = _safe_observe(observe, interface)
        selected = select_bind_address(fixed_host=fixed_host, observed_ip=observed)
        if selected is not None:
            return selected
        delay = next_backoff_seconds(delay)
        _LOGGER.warning(
            "Waiting %.0fs for an IPv4 address on %s (observed=%s, required=%s)",
            delay,
            interface,
            observed,
            fixed_host or "any",
        )
        await asyncio.sleep(delay)


def _safe_observe(observe: Callable[[str], Optional[str]], interface: str) -> Optional[str]:
    try:
        return observe(interface)
    except (ValueError, OSError, KeyError):
        return None
