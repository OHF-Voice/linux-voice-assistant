"""Runs mDNS zeroconf service for Home Assistant discovery."""

import logging
import socket
import uuid
from typing import Optional

_LOGGER = logging.getLogger(__name__)

try:
    from zeroconf.asyncio import AsyncServiceInfo, AsyncZeroconf
except ImportError:
    _LOGGER.fatal("zeroconf not installed. Please install it with: pip install zeroconf")
    raise

MDNS_TARGET_IP = "224.0.0.251"


class HomeAssistantZeroconf:
    def __init__(
        self, port: int, mac_address: str, host_ip_address: str, name: Optional[str] = None, host: Optional[str] = None
    ) -> None:
        self.port = port
        self.mac_address = mac_address
        self.name = name or self.mac_address
        self.host_ip_address = host_ip_address

        self._aiozc = AsyncZeroconf()

    async def register_server(self) -> None:

        service_info = AsyncServiceInfo(
            "_esphomelib._tcp.local.",
            f"{self.name}._esphomelib._tcp.local.",
            addresses=[socket.inet_aton(self.host_ip_address)],
            port=self.port,
            properties={
                "version": "2025.9.0",
                "mac": self.mac_address,
                "board": "host",
                "platform": "HOST",
                "network": "ethernet",  # or "wifi"
            },
            server=f"{self.name}.local.",
        )
        await self._aiozc.async_register_service(service_info)
        _LOGGER.debug("Zeroconf discovery enabled: %s", service_info)

