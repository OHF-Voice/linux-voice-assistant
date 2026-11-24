"""The ESPHome listener and its mDNS announcement as one unit that can move to another address."""

import asyncio
import logging
from typing import TYPE_CHECKING, Awaitable, Callable, Optional

if TYPE_CHECKING:
    from .models import ServerState
    from .zeroconf import HomeAssistantZeroconf

_LOGGER = logging.getLogger(__name__)


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
