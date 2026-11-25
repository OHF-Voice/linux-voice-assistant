"""Runs mDNS zeroconf service for Home Assistant discovery."""

import logging
import socket
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
        self,
        port: int,
        mac_address: str,
        host_ip_address: str,
        name: Optional[str] = None,
        encrypted: bool = False,
    ) -> None:
        self.port = port
        self.encrypted = encrypted
        self.mac_address = mac_address
        self.name = name or self.mac_address
        self.host_ip_address = host_ip_address

        self._aiozc = AsyncZeroconf()

    async def register_server(self) -> None:
        properties = {
            "version": "2025.9.0",
            "mac": self.mac_address,
            "board": "host",
            "platform": "HOST",
            "network": "ethernet",  # or "wifi"
        }
        if self.encrypted:
            # As ESPHome advertises it: Home Assistant then asks for the key when adding the device.
            properties["api_encryption"] = "Noise_NNpsk0_25519_ChaChaPoly_SHA256"

        service_info = AsyncServiceInfo(
            "_esphomelib._tcp.local.",
            f"{self.name}._esphomelib._tcp.local.",
            addresses=[socket.inet_aton(self.host_ip_address)],
            port=self.port,
            properties=properties,
            server=f"{self.name}.local.",
        )
        await self._aiozc.async_register_service(service_info)
        _LOGGER.debug("Zeroconf discovery enabled: %s", service_info)


async def start_discovery(
    enabled: bool,
    port: int,
    mac_address: str,
    host_ip_address: str,
    name: Optional[str] = None,
    encrypted: bool = False,
) -> Optional[HomeAssistantZeroconf]:
    """
    Register the mDNS service for Home Assistant discovery, unless discovery is disabled.
    :param enabled: False skips mDNS completely: no socket is opened and nothing is announced.
    :param port: ESPHome API port to announce.
    :param mac_address: MAC address to announce.
    :param host_ip_address: IP address to announce.
    :param name: Device name to announce, defaults to the MAC address.
    :param encrypted: Announce that the API needs an encryption key.
    :return: The running discovery, or None when it is disabled.
    """
    if not enabled:
        _LOGGER.info("mDNS discovery disabled: add this device manually in Home Assistant with %s:%s", host_ip_address, port)
        return None

    discovery = HomeAssistantZeroconf(
        port=port,
        name=name,
        mac_address=mac_address,
        host_ip_address=host_ip_address,
        encrypted=encrypted,
    )
    await discovery.register_server()
    return discovery
