"""Utility methods."""

import uuid
# netifaces lib is from netifaces2
import netifaces
from collections.abc import Callable
from typing import Optional

def call_all(*callables: Optional[Callable[[], None]]) -> None:
    for item in filter(None, callables):
        item()


def get_default_interface():
    """Return the default network interface name, or None if not found."""
    default_gateway = netifaces.default_gateway()

    if not default_gateway:
        print("No default gateway found")
        return None

    # default_gateway is e.g. {InterfaceType.AF_INET: ('192.168.33.1', 'wlp0s20f3')}
    gateway_info = default_gateway.get(netifaces.AF_INET)
    if not gateway_info:
        print("No default IPv4 gateway found")
        return None

    # gateway_info is a tuple: (gateway_ip, interface_name)
    interface_name = gateway_info[1]
    #print(f"Default interface: {interface_name}")
    return interface_name


def get_default_ipv4(interface: str):
    if not interface:
        return None

    addresses = netifaces.ifaddresses(interface)
    ipv4_info = addresses.get(netifaces.AF_INET)

    if not ipv4_info:
        return None

    return ipv4_info[0]["addr"]
