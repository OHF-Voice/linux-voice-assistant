"""Runs mDNS zeroconf service for Home Assistant discovery."""

import logging
import socket
import uuid
from typing import Optional

_LOGGER = logging.getLogger(__name__)

try:
    from zeroconf.asyncio import AsyncServiceInfo, AsyncZeroconf
except ImportError:
    _LOGGER.fatal("pip install zeroconf")
    raise

MDNS_TARGET_IP = "224.0.0.251"


class HomeAssistantZeroconf:
    def __init__(
        self, port: int, name: Optional[str] = None, host: Optional[str] = None
    ) -> None:
        self.port = port
        self.name = name or _get_mac_address()

        if not host:
            test_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            test_sock.setblocking(False)
            test_sock.connect((MDNS_TARGET_IP, 1))
            host = test_sock.getsockname()[0]
            _LOGGER.debug("Detected IP: %s", host)

        assert host
        self.host = host
        self._aiozc = AsyncZeroconf()
        self.service_info = None

    async def register_server(self) -> None:

        self.service_info = AsyncServiceInfo(
            "_esphomelib._tcp.local.",
            f"{self.name}._esphomelib._tcp.local.",
            addresses=[socket.inet_aton(self.host)],
            port=self.port,
            properties={
                "version": "2025.9.0",
                "mac": _get_mac_address(),
                "board": "host",
                "platform": "HOST",
                "network": "wifi",  # or "ethernet"
            },
            server=f"{self.name}.local.",
        )
        await self._aiozc.async_register_service(self.service_info)
        _LOGGER.debug("Zeroconf discovery enabled: %s", self.service_info)

    async def unregister_server(self) -> None:
        if self.service_info is not None:
            await self._aiozc.async_unregister_service(self.service_info)
            await self._aiozc.async_close()
            _LOGGER.debug("Zeroconf discovery disabled")

def _get_mac_address() -> str:
    """Return MAC address formatted as hex with no colons."""
    return "".join(
        # pylint: disable=consider-using-f-string
        ["{:02x}".format((uuid.getnode() >> ele) & 0xFF) for ele in range(0, 8 * 6, 8)][
            ::-1
        ]
    )
