"""Move the ESPHome listener and the mDNS announcement to a new interface address after a client disconnected.

The ESPHome server listens on the address the network interface had at startup, and the same address is announced over mDNS. After
a Wi-Fi drop or a DHCP renewal the interface may have another address, and Home Assistant could not reach LVA any more. Whenever a
client disconnects, the interface address is read again here and, if it changed, the listener and the announcement follow.
"""

import asyncio
import logging
from typing import Awaitable, Callable, Optional

from .util import get_default_ipv4

_LOGGER = logging.getLogger(__name__)

RETRY_DELAY_INITIAL_SECONDS = 1.0
RETRY_DELAY_CAP_SECONDS = 60.0


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


async def refresh_address_after_disconnect(
    *,
    interface: str,
    bound_ip: str,
    client_disconnected: asyncio.Event,
    apply: Callable[[str], Awaitable[None]],
    observe: Callable[[str], Optional[str]] = read_interface_ipv4,
) -> None:
    """
    After every client disconnect, check the interface address and apply it if it changed. Runs until cancelled.
    :param interface: Interface the listener address was taken from.
    :param bound_ip: Address the listener is bound to at the start.
    :param client_disconnected: Set whenever a client disconnected.
    :param apply: Moves the listener and the announcement to the given address. If it raises, it is tried again with a growing delay.
    :param observe: Returns the interface's IPv4 address, or None when it has none.

    While the interface has no address, or applying fails, the check is repeated after 1 s, 2 s, 4 s and so on up to 60 s.
    """
    while True:
        await client_disconnected.wait()
        client_disconnected.clear()
        delay = 0.0
        while True:
            observed_ip = observe(interface)
            if observed_ip is not None:
                new_ip = changed_address(bound_ip=bound_ip, observed_ip=observed_ip)
                if new_ip is None:
                    _LOGGER.debug("Client disconnected: interface %s still has %s", interface, bound_ip)
                    break
                _LOGGER.info("Client disconnected: interface %s has %s now, listener is on %s", interface, new_ip, bound_ip)
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
